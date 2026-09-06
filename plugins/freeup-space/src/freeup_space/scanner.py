"""Read-only filesystem traversal with cancellation and progress reporting."""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable, Iterable, List, Optional, Set, Tuple
from uuid import uuid4

from .models import FileRecord, ScanError, ScanProgress, ScanRun
from .policy import Policy


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class FilesystemAdapter:
    """Narrow filesystem seam used by traversal and platform fault handling."""

    def lstat(self, path: Path):
        return os.lstat(path)

    def scandir(self, path: Path):
        return os.scandir(path)


def _is_link_or_reparse(stat_result) -> bool:
    return stat.S_ISLNK(stat_result.st_mode) or bool(
        getattr(stat_result, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _allocated_size(stat_result) -> Optional[int]:
    blocks = getattr(stat_result, "st_blocks", None)
    return blocks * 512 if blocks is not None else None


def _owner(stat_result) -> Optional[str]:
    uid = getattr(stat_result, "st_uid", None)
    if uid is None:
        return None
    try:
        import pwd

        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return str(uid)


def _record(path: Path, stat_result) -> FileRecord:
    device = int(stat_result.st_dev)
    inode = int(stat_result.st_ino)
    return FileRecord(
        path=path,
        size=int(stat_result.st_size),
        allocated_size=_allocated_size(stat_result),
        mtime=float(stat_result.st_mtime),
        atime=float(stat_result.st_atime),
        ctime=float(stat_result.st_ctime),
        st_dev=device,
        st_ino=inode,
        volume_id=str(device),
        file_id="{}:{}".format(device, inode),
        owner=_owner(stat_result),
    )


def scan_paths(
    roots: Iterable[Path],
    policy: Policy,
    progress: Callable[[ScanProgress], None],
    cancel: Event,
    *,
    adapter: Optional[FilesystemAdapter] = None,
    max_errors: int = 1000,
) -> ScanRun:
    """Scan regular-file metadata without following links or rewriting sources."""

    if max_errors < 0:
        raise ValueError("max_errors must be non-negative")
    filesystem = adapter or FilesystemAdapter()
    started_at = datetime.now(timezone.utc)
    files: List[FileRecord] = []
    errors: List[ScanError] = []
    seen_files: Set[Tuple[int, int]] = set()
    seen_directories: Set[Tuple[int, int]] = set()
    directories_scanned = 0
    bytes_scanned = 0
    skipped_links = 0
    pending = [(Path(root), None) for root in reversed(tuple(roots))]

    def report(current_path: Optional[Path], complete: bool = False) -> None:
        progress(
            ScanProgress(
                files_scanned=len(files),
                bytes_scanned=bytes_scanned,
                directories_scanned=directories_scanned,
                errors=len(errors),
                skipped_links=skipped_links,
                complete=complete,
                cancelled=cancel.is_set(),
                current_path=current_path,
            )
        )

    def record_error(path: Path, operation: str, error: OSError) -> None:
        if len(errors) < max_errors:
            errors.append(
                ScanError(
                    path=path,
                    operation=operation,
                    error_type=type(error).__name__,
                    message=str(error),
                )
            )

    while pending and not cancel.is_set():
        path, root_device = pending.pop()
        try:
            path_stat = filesystem.lstat(path)
        except OSError as error:
            record_error(path, "lstat", error)
            report(path)
            continue

        if _is_link_or_reparse(path_stat):
            skipped_links += 1
            report(path)
            continue

        identity = (int(path_stat.st_dev), int(path_stat.st_ino))
        if stat.S_ISREG(path_stat.st_mode):
            if identity not in seen_files:
                seen_files.add(identity)
                file_record = _record(path, path_stat)
                files.append(file_record)
                bytes_scanned += file_record.size
            report(path)
            continue
        if not stat.S_ISDIR(path_stat.st_mode):
            report(path)
            continue

        expected_device = int(path_stat.st_dev) if root_device is None else root_device
        if int(path_stat.st_dev) != expected_device or identity in seen_directories:
            report(path)
            continue
        seen_directories.add(identity)
        directories_scanned += 1
        try:
            iterator = filesystem.scandir(path)
            with iterator:
                child_paths = sorted(
                    (Path(entry.path) for entry in iterator), key=lambda item: item.name
                )
        except OSError as error:
            record_error(path, "scandir", error)
            report(path)
            continue
        for child_path in reversed(child_paths):
            pending.append((child_path, expected_device))
        report(path)

    completed_at = datetime.now(timezone.utc)
    complete = not cancel.is_set()
    report(None, complete=complete)
    return ScanRun(
        run_id=uuid4().hex,
        platform=policy.platform,
        started_at=started_at,
        completed_at=completed_at,
        complete=complete,
        files=tuple(files),
        errors=tuple(errors),
    )


__all__ = [
    "FileRecord",
    "FilesystemAdapter",
    "ScanError",
    "ScanProgress",
    "scan_paths",
]
