"""Selection application with explicit revalidation and receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shutil import disk_usage
from typing import Iterable

from .models import CleanupPlan, Receipt
from .plans import select_ids
from .policy import Policy
from .revalidate import revalidate
from .trash_macos import move_to_trash as move_to_macos_trash
from .trash_windows import move_to_trash as move_to_windows_trash


@dataclass(frozen=True)
class MoveResult:
    path: Path
    moved: bool
    bytes_moved: int
    backend: str


@dataclass(frozen=True)
class ApplySkipped:
    candidate_id: str
    path: Path
    reason: str


@dataclass(frozen=True)
class ApplyMoved:
    candidate_id: str
    path: Path
    bytes_moved: int
    backend: str


def _free_space_root(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    return Path(resolved.anchor or str(resolved))


def _move(path: Path, platform: str) -> MoveResult:
    bytes_moved = path.lstat().st_size
    if platform == "macos":
        move_to_macos_trash(path, platform)
        backend = "finder-trash"
    elif platform == "windows":
        move_to_windows_trash(path, platform)
        backend = "recycle-bin"
    else:
        raise ValueError("unsupported platform {!r}".format(platform))
    return MoveResult(
        path=path,
        moved=True,
        bytes_moved=bytes_moved,
        backend=backend,
    )


def apply_selection(
    plan: CleanupPlan,
    ids: Iterable[str],
    platform: str,
    dry_run: bool = False,
) -> Receipt:
    """Revalidate and apply only the explicitly approved candidate IDs."""

    selection = select_ids(plan, ids)
    policy = Policy.for_platform(platform)
    free_root = _free_space_root(selection.candidates[0].path)
    free_space_before = disk_usage(str(free_root)).free
    moved = []
    skipped = []
    failed = []
    logical_moved_bytes = 0

    for candidate in selection.candidates:
        decision = revalidate(candidate, policy)
        if not decision.actionable:
            skipped.append(
                ApplySkipped(
                    candidate_id=candidate.candidate_id,
                    path=candidate.path,
                    reason=decision.reason,
                )
            )
            continue
        if dry_run:
            skipped.append(
                ApplySkipped(
                    candidate_id=candidate.candidate_id,
                    path=candidate.path,
                    reason="dry-run",
                )
            )
            continue
        try:
            result = _move(candidate.path, platform)
        except Exception as error:  # pragma: no cover - exercised by platform adapters
            failed.append(
                ApplySkipped(
                    candidate_id=candidate.candidate_id,
                    path=candidate.path,
                    reason=str(error),
                )
            )
            continue
        moved.append(
            ApplyMoved(
                candidate_id=candidate.candidate_id,
                path=result.path,
                bytes_moved=result.bytes_moved,
                backend=result.backend,
            )
        )
        logical_moved_bytes += result.bytes_moved

    free_space_after = disk_usage(str(free_root)).free
    return Receipt(
        run_id=plan.run_id,
        platform=platform,
        created_at=datetime.now(timezone.utc),
        approved_ids=selection.candidate_ids,
        moved=tuple(moved),
        skipped=tuple(skipped),
        failed=tuple(failed),
        logical_moved_bytes=logical_moved_bytes,
        free_space_before=free_space_before,
        free_space_after=free_space_after,
        plan_digest=selection.plan_digest,
    )
