"""Guarded permanent deletion for explicitly approved plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shutil import disk_usage
from typing import Iterable

from .models import CleanupPlan, Receipt
from .plans import PlanError, select_ids
from .policy import Policy
from .revalidate import revalidate


class ApprovalError(ValueError):
    """Permanent deletion was not explicitly and safely approved."""


@dataclass(frozen=True)
class DeleteResult:
    path: Path
    deleted: bool
    bytes_deleted: int


def _delete(path: Path) -> DeleteResult:
    bytes_deleted = path.lstat().st_size
    path.unlink()
    return DeleteResult(path=path, deleted=True, bytes_deleted=bytes_deleted)


def permanently_delete(plan: CleanupPlan, ids: Iterable[str], platform: str) -> Receipt:
    """Permanently delete only a permanent-delete plan with explicit IDs."""

    if plan.action.value != "permanent-delete":
        raise ApprovalError("permanent delete requires a permanent-delete plan")

    try:
        selection = select_ids(plan, ids)
    except PlanError as error:
        raise ApprovalError(str(error)) from error

    if selection.action.value != "permanent-delete":
        raise ApprovalError("permanent delete requires a permanent-delete plan")
    actionable_ids = {
        candidate.candidate_id
        for candidate in plan.candidates
        if candidate.actionable and candidate.risk.value != "report-only"
    }
    if set(selection.candidate_ids) != actionable_ids:
        raise ApprovalError("permanent delete requires exact actionable candidate IDs")

    policy = Policy.for_platform(platform)
    free_root = Path(selection.candidates[0].path).resolve(strict=False).anchor or "/"
    free_space_before = disk_usage(free_root).free
    deleted = []
    skipped = []
    failed = []
    logical_deleted_bytes = 0

    for candidate in selection.candidates:
        decision = revalidate(candidate, policy)
        if not decision.actionable:
            skipped.append((candidate.candidate_id, candidate.path, decision.reason))
            continue
        snapshot = candidate.snapshot
        current = candidate.path.lstat()
        if (
            snapshot.path is not None
            and Path(snapshot.path).resolve(strict=False) != candidate.path.resolve(strict=False)
        ) or current.st_dev != snapshot.st_dev or current.st_ino != snapshot.st_ino:
            skipped.append((candidate.candidate_id, candidate.path, "snapshot-mismatch"))
            continue
        if current.st_size != snapshot.size or current.st_mtime != snapshot.mtime:
            skipped.append((candidate.candidate_id, candidate.path, "snapshot-mismatch"))
            continue
        try:
            result = _delete(candidate.path)
        except Exception as error:  # pragma: no cover - platform/filesystem dependent
            failed.append((candidate.candidate_id, candidate.path, str(error)))
            continue
        deleted.append((candidate.candidate_id, result.path, result.bytes_deleted))
        logical_deleted_bytes += result.bytes_deleted

    free_space_after = disk_usage(free_root).free
    return Receipt(
        run_id=plan.run_id,
        platform=platform,
        created_at=datetime.now(timezone.utc),
        approved_ids=selection.candidate_ids,
        moved=tuple(),
        skipped=tuple(skipped),
        failed=tuple(failed),
        logical_moved_bytes=logical_deleted_bytes,
        free_space_before=free_space_before,
        free_space_after=free_space_after,
        plan_digest=selection.plan_digest,
    )
