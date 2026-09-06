from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from freeup_space.models import (
    ActionType,
    Candidate,
    CandidateSnapshot,
    CleanupPlan,
    Risk,
)
from freeup_space.permanent_delete import ApprovalError, permanently_delete


def _snapshot(path: Path) -> CandidateSnapshot:
    stat = path.lstat()
    return CandidateSnapshot(
        st_dev=stat.st_dev,
        st_ino=stat.st_ino,
        size=stat.st_size,
        mtime=stat.st_mtime,
        path=path,
        safe_root=path.parent,
    )


@pytest.fixture
def trash_plan(tmp_path):
    target = tmp_path / "trash.bin"
    target.write_bytes(b"trash")
    candidate = Candidate(
        candidate_id="OLD-001",
        path=target,
        category="old-file",
        reasons=("old file",),
        risk=Risk.MEDIUM,
        actionable=True,
        snapshot=_snapshot(target),
        size=target.stat().st_size,
        reclaimable_bytes=target.stat().st_size,
        created_at=datetime.now(timezone.utc),
    )
    return CleanupPlan(
        schema_version="1",
        run_id="run-trash",
        platform="macos",
        created_at=datetime.now(timezone.utc),
        policy_version="1",
        candidates=(candidate,),
        digest="t" * 64,
        action=ActionType.TRASH,
    )


@pytest.fixture
def permanent_plan(tmp_path):
    target = tmp_path / "permanent.bin"
    target.write_bytes(b"permanent")
    candidate = Candidate(
        candidate_id="OLD-001",
        path=target,
        category="old-file",
        reasons=("old file",),
        risk=Risk.MEDIUM,
        actionable=True,
        snapshot=_snapshot(target),
        size=target.stat().st_size,
        reclaimable_bytes=target.stat().st_size,
        created_at=datetime.now(timezone.utc),
    )
    return CleanupPlan(
        schema_version="1",
        run_id="run-permanent",
        platform="windows",
        created_at=datetime.now(timezone.utc),
        policy_version="1",
        candidates=(candidate,),
        digest="p" * 64,
        action=ActionType.PERMANENT_DELETE,
    )


def test_trash_plan_cannot_be_permanently_deleted(trash_plan):
    with pytest.raises(ApprovalError):
        permanently_delete(trash_plan, ["OLD-001"], "macos")


def test_permanent_delete_requires_exact_ids(permanent_plan):
    with pytest.raises(ApprovalError):
        permanently_delete(permanent_plan, [], "windows")
