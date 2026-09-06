from datetime import datetime, timezone

import pytest

from freeup_space.apply import apply_selection
from freeup_space.models import Candidate, CandidateSnapshot, CleanupPlan, Risk


@pytest.fixture
def sample_plan(tmp_path):
    target = tmp_path / "candidate.bin"
    target.write_bytes(b"original")
    stat = target.lstat()
    snapshot = CandidateSnapshot(
        st_dev=stat.st_dev,
        st_ino=stat.st_ino,
        size=stat.st_size,
        mtime=stat.st_mtime,
        path=target,
        safe_root=tmp_path,
    )
    candidate = Candidate(
        candidate_id="OLD-001",
        path=target,
        category="old-file",
        reasons=("old file",),
        risk=Risk.MEDIUM,
        actionable=True,
        snapshot=snapshot,
        size=stat.st_size,
        reclaimable_bytes=stat.st_size,
        created_at=datetime.now(timezone.utc),
    )
    cache_target = tmp_path / "cache.bin"
    cache_target.write_bytes(b"cache")
    cache_stat = cache_target.lstat()
    cache_snapshot = CandidateSnapshot(
        st_dev=cache_stat.st_dev,
        st_ino=cache_stat.st_ino,
        size=cache_stat.st_size,
        mtime=cache_stat.st_mtime,
        path=cache_target,
        safe_root=tmp_path,
    )
    cache_candidate = Candidate(
        candidate_id="CACHE-001",
        path=cache_target,
        category="cache",
        reasons=("cache data",),
        risk=Risk.LOW,
        actionable=True,
        snapshot=cache_snapshot,
        size=cache_stat.st_size,
        reclaimable_bytes=cache_stat.st_size,
        created_at=datetime.now(timezone.utc),
    )
    return CleanupPlan(
        schema_version="1",
        run_id="run-1",
        platform="macos",
        created_at=datetime.now(timezone.utc),
        policy_version="1",
        candidates=(candidate, cache_candidate),
        digest="d" * 64,
    )


def test_modified_file_is_skipped(sample_plan):
    target = sample_plan.candidates[0].path
    target.write_bytes(b"changed")

    receipt = apply_selection(sample_plan, ["OLD-001"], "macos", dry_run=True)

    assert receipt.skipped[0].reason == "snapshot-mismatch"


def test_empty_trash_is_never_called(monkeypatch, sample_plan):
    def fail_if_called():
        raise AssertionError("empty_trash should never be called")

    monkeypatch.setattr("freeup_space.trash_macos.empty_trash", fail_if_called)

    receipt = apply_selection(sample_plan, ["CACHE-001"], "macos", dry_run=True)

    assert receipt.skipped[0].reason == "dry-run"
