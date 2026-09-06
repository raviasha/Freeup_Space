from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from freeup_space.models import (
    ActionType,
    Candidate,
    CandidateSnapshot,
    CleanupPlan,
    Evidence,
    FileRecord,
    Risk,
    ScanRun,
)
from freeup_space.plans import build_permanent_plan
from freeup_space.policy import Policy
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


@pytest.fixture
def mixed_permanent_plan(tmp_path):
    actionable = tmp_path / "actionable.bin"
    actionable.write_bytes(b"actionable")
    report_only = tmp_path / "report-only.bin"
    report_only.write_bytes(b"report")

    files = (
        FileRecord(
            path=actionable,
            size=actionable.stat().st_size,
            allocated_size=actionable.stat().st_size,
            mtime=actionable.stat().st_mtime,
            atime=actionable.stat().st_atime,
            ctime=actionable.stat().st_ctime,
            st_dev=actionable.stat().st_dev,
            st_ino=actionable.stat().st_ino,
            volume_id="volume-a",
            file_id="7:101",
        ),
        FileRecord(
            path=report_only,
            size=report_only.stat().st_size,
            allocated_size=report_only.stat().st_size,
            mtime=report_only.stat().st_mtime,
            atime=report_only.stat().st_atime,
            ctime=report_only.stat().st_ctime,
            st_dev=report_only.stat().st_dev,
            st_ino=report_only.stat().st_ino,
            volume_id="volume-a",
            file_id="7:102",
        ),
    )
    run = ScanRun(
        run_id="run-mixed",
        platform="macos",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        complete=True,
        files=files,
    )
    evidence = (
        Evidence(
            path=actionable,
            category="old-file",
            rule="old-file",
            reason="actionable cleanup candidate",
            risk=Risk.MEDIUM,
            actionable=True,
            size=actionable.stat().st_size,
        ),
        Evidence(
            path=report_only,
            category="system-managed",
            rule="protected-file",
            reason="report-only path",
            risk=Risk.REPORT_ONLY,
            actionable=False,
            size=report_only.stat().st_size,
        ),
    )
    policy = Policy.for_platform("macos")
    return build_permanent_plan(run, evidence, policy)


def test_trash_plan_cannot_be_permanently_deleted(trash_plan):
    with pytest.raises(ApprovalError):
        permanently_delete(trash_plan, ["OLD-001"], "macos")


def test_permanent_delete_requires_exact_ids(permanent_plan):
    with pytest.raises(ApprovalError):
        permanently_delete(permanent_plan, [], "windows")


def test_permanent_delete_requires_all_actionable_ids_and_rejects_report_only(
    mixed_permanent_plan,
):
    actionable_id = next(
        candidate.candidate_id
        for candidate in mixed_permanent_plan.candidates
        if candidate.actionable
    )
    report_only_id = next(
        candidate.candidate_id
        for candidate in mixed_permanent_plan.candidates
        if candidate.risk is Risk.REPORT_ONLY
    )

    receipt = permanently_delete(mixed_permanent_plan, [actionable_id], "macos")

    assert receipt.approved_ids == (actionable_id,)

    with pytest.raises(ApprovalError, match="report-only"):
        permanently_delete(mixed_permanent_plan, [actionable_id, report_only_id], "macos")
