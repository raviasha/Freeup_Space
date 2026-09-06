import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from freeup_space.apply import apply_selection
from freeup_space.categories import classify
from freeup_space.old_files import find_old_files
from freeup_space.plans import build_plan
from freeup_space.policy import Policy
from freeup_space.reports import render_markdown
from freeup_space.scanner import scan_paths


FIXED_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def months_before(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, 28)
    return value.replace(year=year, month=month, day=day)


def make_full_fixture(tmp_path: Path):
    root = tmp_path / "fixture"
    downloads = root / "Downloads"
    protected_dir = root / "System"
    downloads.mkdir(parents=True, exist_ok=True)
    protected_dir.mkdir(parents=True, exist_ok=True)

    cache_path = downloads / "cache.bin"
    cache_path.write_bytes(b"cache payload")
    cache_time = months_before(FIXED_NOW, 13).timestamp()
    os.utime(cache_path, (cache_time, cache_time))

    protected_path = protected_dir / "sleepimage"
    protected_path.write_bytes(b"protected payload")

    return {
        "root": root,
        "cache_path": cache_path,
        "protected_path": protected_path,
    }


def audit(root: Path, platform: str):
    run = scan_paths([root], Policy.for_platform(platform), lambda _: None, Event())
    policy = Policy.for_platform(platform)
    evidence = []
    for record in run.files:
        evidence.extend(classify(record, policy))
    evidence.extend(find_old_files(run.files, run.completed_at or run.started_at))
    return run, tuple(evidence)


def test_full_fixture_flow_never_touches_protected_file(tmp_path, monkeypatch):
    fixture = make_full_fixture(tmp_path)
    policy = replace(
        Policy.for_platform("macos"),
        protected_files=(str(fixture["protected_path"]),),
        safe_roots=(str(fixture["root"] / "Downloads"),),
    )

    monkeypatch.setattr("freeup_space.policy.Policy.for_platform", lambda platform: policy)

    run, evidence = audit(fixture["root"], platform="macos")
    plan = build_plan(run, evidence, Policy.for_platform("macos"))
    report = render_markdown(plan)
    cache_id = next(candidate.candidate_id for candidate in plan.candidates if candidate.path == fixture["cache_path"])
    protected_id = next(candidate.candidate_id for candidate in plan.candidates if candidate.path == fixture["protected_path"])

    receipt = apply_selection(plan, [cache_id], "macos", dry_run=True)

    assert cache_id in report
    assert protected_id in report
    assert receipt.approved_ids == (cache_id,)
    assert receipt.moved == ()
    assert fixture["protected_path"].exists()
