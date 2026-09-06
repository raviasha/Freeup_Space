from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from freeup_space.models import (
    ActionType,
    DuplicateGroup,
    Evidence,
    FileRecord,
    Risk,
    ScanRun,
)
from freeup_space.plans import PlanError, build_plan, select_ids
from freeup_space.policy import Policy


NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def record(path: Path, *, size: int, inode: int, volume: str = "volume-a") -> FileRecord:
    return FileRecord(
        path=path,
        size=size,
        allocated_size=size + 512,
        mtime=1_700_000_000.25,
        atime=1_699_000_000.0,
        ctime=1_698_000_000.0,
        st_dev=7,
        st_ino=inode,
        volume_id=volume,
        file_id="7:{}".format(inode),
        owner="501",
        flags=("hidden",),
    )


def finding(
    path: Path,
    *,
    category: str,
    reason: str,
    risk: Risk,
    actionable: bool,
    size: int,
    rule: str,
) -> Evidence:
    return Evidence(
        path=path,
        category=category,
        rule=rule,
        reason=reason,
        risk=risk,
        actionable=actionable,
        size=size,
    )


@pytest.fixture
def sample_plan(tmp_path):
    downloads = tmp_path / "Downloads"
    duplicate = downloads / "copy.bin"
    retained = downloads / "original.bin"
    protected = Path("/System/Library/large.db")
    files = (
        record(duplicate, size=4096, inode=11),
        record(retained, size=4096, inode=12),
        record(protected, size=8192, inode=13, volume="system"),
    )
    run = ScanRun(
        run_id="run-5",
        platform="macos",
        started_at=NOW,
        completed_at=NOW,
        complete=True,
        files=files,
        duplicate_groups=(
            DuplicateGroup(
                group_id="DUP-GROUP",
                digest="a" * 64,
                size=4096,
                retained_path=retained,
                duplicate_paths=(duplicate,),
                reclaimable_bytes=4096,
            ),
        ),
    )
    evidence = (
        finding(
            duplicate,
            category="download",
            reason="downloaded user data",
            risk=Risk.MEDIUM,
            actionable=True,
            size=4096,
            rule="download-location",
        ),
        finding(
            duplicate,
            category="old-file",
            reason="not modified for at least 12 months",
            risk=Risk.MEDIUM,
            actionable=True,
            size=4096,
            rule="old-file-12-months",
        ),
        finding(
            protected,
            category="system-managed",
            reason="database consistency is unknown",
            risk=Risk.REPORT_ONLY,
            actionable=False,
            size=8192,
            rule="database-extension",
        ),
    )
    policy = replace(
        Policy.for_platform("macos"), safe_roots=(str(downloads),)
    )
    return build_plan(run, evidence, policy)


def test_build_plan_rejects_incomplete_scan():
    run = ScanRun(run_id="unfinished", platform="macos", started_at=NOW)

    with pytest.raises(PlanError, match="complete scan"):
        build_plan(run, (), Policy.for_platform("macos"))


def test_build_plan_has_stable_ids_digest_and_complete_snapshots(sample_plan, tmp_path):
    duplicate = next(item for item in sample_plan.candidates if item.category == "duplicate")

    assert [item.candidate_id for item in sample_plan.candidates] == [
        "DUP-001",
        "SYS-001",
    ]
    assert len(sample_plan.digest) == 64
    assert duplicate.snapshot.to_dict() == {
        "st_dev": 7,
        "st_ino": 11,
        "size": 4096,
        "mtime": 1_700_000_000.25,
        "digest": "a" * 64,
        "path": str(tmp_path / "Downloads" / "copy.bin"),
        "safe_root": str(tmp_path / "Downloads"),
    }
    assert duplicate.allocated_size == 4608
    assert duplicate.file_id == "7:11"
    assert duplicate.owner == "501"
    assert duplicate.flags == ("hidden",)


def test_build_plan_is_reproducible_and_nested_evidence_is_immutable(sample_plan):
    with pytest.raises(FrozenInstanceError):
        sample_plan.candidates[0].category = "changed"
    with pytest.raises(TypeError):
        sample_plan.candidates[0].evidence["rules"] = ("changed",)


def test_same_inputs_produce_same_ids_and_digest(tmp_path):
    path = tmp_path / "Downloads" / "archive.zip"
    item = record(path, size=12_345, inode=21)
    run = ScanRun(
        run_id="repeatable",
        platform="macos",
        started_at=NOW,
        completed_at=NOW,
        complete=True,
        files=(item,),
    )
    evidence = (
        finding(
            path,
            category="archive",
            reason="archive or disk image",
            risk=Risk.MEDIUM,
            actionable=True,
            size=12_345,
            rule="archive-extension",
        ),
    )
    policy = replace(Policy.for_platform("macos"), safe_roots=(str(tmp_path),))

    first = build_plan(run, evidence, policy)
    second = build_plan(run, evidence, policy)

    assert first == second
    assert first.candidates[0].candidate_id == "ARC-001"


def test_select_ids_requires_known_actionable_report_candidates(sample_plan):
    with pytest.raises(PlanError, match="unknown candidate ID"):
        select_ids(sample_plan, ["OLD-999"])
    with pytest.raises(PlanError, match="report-only"):
        select_ids(sample_plan, ["SYS-001"])
    with pytest.raises(PlanError, match="at least one explicit candidate ID"):
        select_ids(sample_plan, [])

    selection = select_ids(sample_plan, ["DUP-001"])

    assert selection.candidate_ids == ("DUP-001",)
    assert selection.candidates == (sample_plan.candidates[0],)
    assert selection.reclaimable_bytes == 4096
    assert selection.plan_digest == sample_plan.digest
    assert selection.action is ActionType.TRASH


def test_select_ids_rejects_candidate_marked_non_actionable(sample_plan):
    candidate = sample_plan.candidates[0]
    unsafe_candidate = replace(candidate, actionable=False)
    unsafe_plan = replace(sample_plan, candidates=(unsafe_candidate,))

    with pytest.raises(PlanError, match="non-actionable"):
        select_ids(unsafe_plan, [unsafe_candidate.candidate_id])


def test_report_only_policy_boundary_overrides_duplicate_primary_category():
    retained = Path("/System/Library/retained.db")
    duplicate = Path("/System/Library/duplicate.db")
    run = ScanRun(
        run_id="protected-duplicate",
        platform="macos",
        started_at=NOW,
        completed_at=NOW,
        complete=True,
        files=(
            record(retained, size=5000, inode=31, volume="system"),
            record(duplicate, size=5000, inode=32, volume="system"),
        ),
        duplicate_groups=(
            DuplicateGroup(
                group_id="DUP-PROTECTED",
                digest="b" * 64,
                size=5000,
                retained_path=retained,
                duplicate_paths=(duplicate,),
                reclaimable_bytes=5000,
            ),
        ),
    )
    evidence = (
        finding(
            duplicate,
            category="system-managed",
            reason="protected operating-system path",
            risk=Risk.REPORT_ONLY,
            actionable=False,
            size=5000,
            rule="protected-path",
        ),
    )

    plan = build_plan(run, evidence, Policy.for_platform("macos"))

    assert plan.candidates[0].candidate_id == "SYS-001"
    assert plan.candidates[0].category == "system-managed"
    assert plan.candidates[0].reclaimable_bytes == 0


def test_duplicate_group_rejects_missing_or_mismatched_retained_inventory_record(tmp_path):
    retained = tmp_path / "kept.bin"
    duplicate = tmp_path / "copy.bin"
    run = ScanRun(
        run_id="invalid-duplicate",
        platform="macos",
        started_at=NOW,
        completed_at=NOW,
        complete=True,
        files=(
            record(duplicate, size=1024, inode=41),
            record(retained, size=2048, inode=42),
        ),
        duplicate_groups=(
            DuplicateGroup(
                group_id="DUP-BAD",
                digest="c" * 64,
                size=1024,
                retained_path=retained,
                duplicate_paths=(duplicate,),
                reclaimable_bytes=1024,
            ),
        ),
    )

    with pytest.raises(PlanError, match="invalid duplicate group"):
        build_plan(run, (), Policy.for_platform("macos"))


def test_plan_order_is_risk_then_reclaimable_bytes_then_category_then_path(tmp_path):
    paths = {
        "small_cache": tmp_path / "cache-b.bin",
        "large_cache": tmp_path / "cache-a.bin",
        "archive": tmp_path / "archive.zip",
        "personal": tmp_path / "photo.jpg",
        "unknown": tmp_path / "opaque.bin",
    }
    sizes = {
        "small_cache": 1000,
        "large_cache": 2000,
        "archive": 4000,
        "personal": 8000,
        "unknown": 16_000,
    }
    categories = {
        "small_cache": "cache",
        "large_cache": "cache",
        "archive": "archive",
        "personal": "personal-data",
        "unknown": "unclassified",
    }
    risks = {
        "small_cache": Risk.LOW,
        "large_cache": Risk.LOW,
        "archive": Risk.MEDIUM,
        "personal": Risk.HIGH,
        "unknown": Risk.REPORT_ONLY,
    }
    records = tuple(
        record(path, size=sizes[name], inode=40 + index)
        for index, (name, path) in enumerate(paths.items())
    )
    evidence = tuple(
        finding(
            path,
            category=categories[name],
            reason=name,
            risk=risks[name],
            actionable=risks[name] is not Risk.REPORT_ONLY,
            size=sizes[name],
            rule=name,
        )
        for name, path in paths.items()
    )
    run = ScanRun(
        run_id="ordered",
        platform="macos",
        started_at=NOW,
        completed_at=NOW,
        complete=True,
        files=records,
    )

    plan = build_plan(run, evidence, Policy.for_platform("macos"))

    assert [item.path for item in plan.candidates] == [
        paths["large_cache"],
        paths["small_cache"],
        paths["archive"],
        paths["personal"],
        paths["unknown"],
    ]


@pytest.mark.parametrize(
    "case",
    ("missing-retained", "same-path", "wrong-size", "same-identity", "bad-digest"),
)
def test_duplicate_group_requires_a_distinct_compatible_retained_record(tmp_path, case):
    retained = tmp_path / "retained.bin"
    duplicate = tmp_path / "duplicate.bin"
    retained_record = record(retained, size=5000, inode=71)
    duplicate_record = record(duplicate, size=5000, inode=72)
    retained_path = retained
    digest = "c" * 64

    if case == "missing-retained":
        files = (duplicate_record,)
    elif case == "same-path":
        files = (duplicate_record,)
        retained_path = duplicate
    elif case == "wrong-size":
        files = (record(retained, size=4999, inode=71), duplicate_record)
    elif case == "same-identity":
        files = (record(retained, size=5000, inode=72), duplicate_record)
    else:
        files = (retained_record, duplicate_record)
        digest = "not-a-sha256"

    run = ScanRun(
        run_id="invalid-{}".format(case),
        platform="macos",
        started_at=NOW,
        completed_at=NOW,
        complete=True,
        files=files,
        duplicate_groups=(
            DuplicateGroup(
                group_id="DUP-INVALID",
                digest=digest,
                size=5000,
                retained_path=retained_path,
                duplicate_paths=(duplicate,),
                reclaimable_bytes=5000,
            ),
        ),
    )

    with pytest.raises(PlanError, match="invalid duplicate group"):
        build_plan(run, (), Policy.for_platform("macos"))
