"""Read-only filesystem traversal with cancellation and progress reporting."""

from __future__ import annotations

import errno
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


class UnsupportedNoFollowError(OSError):
    """Raised when the host cannot atomically enumerate without following links."""


class FilesystemAdapter:
    """Narrow filesystem seam used by traversal and platform fault handling."""

    def lstat(self, path: Path):
        return os.lstat(path)

    def scandir(self, path: Path):
        return os.scandir(path)

    def stat_entry(self, directory: Path, entry):
        if os.name == "nt":
            # Windows DirEntry.stat returns zero device/inode IDs. Read fresh
            # no-follow metadata while scan_directory holds the parent chain.
            return os.lstat(directory / entry.name)
        return entry.stat(follow_symlinks=False)

    def scan_directory(self, path: Path, expected_stat):
        """Open and snapshot one unchanged directory without following its leaf."""

        if os.name == "nt":
            from .windows_fs import locked_directory
            with locked_directory(path):
                actual_stat = os.lstat(path)
                _require_same_directory(path, expected_stat, actual_stat)
                entries, errors = self._read_entries(path, os.scandir(path))
                return actual_stat, entries, errors

        if (
            hasattr(os, "O_DIRECTORY")
            and hasattr(os, "O_NOFOLLOW")
            and os.scandir in os.supports_fd
            and os.open in os.supports_dir_fd
        ):
            descriptors, component_stats = _open_directory_chain(path)
            try:
                directory_stat = component_stats[-1]
                _require_same_directory(path, expected_stat, directory_stat)
                entries, errors = self._read_entries(
                    path, os.scandir(descriptors[-1])
                )
                verification_descriptors, verification_stats = (
                    _open_directory_chain(path)
                )
                try:
                    _require_same_chain(path, component_stats, verification_stats)
                finally:
                    _close_descriptors(verification_descriptors)
            finally:
                _close_descriptors(descriptors)
            return directory_stat, entries, errors

        raise UnsupportedNoFollowError(
            errno.ENOTSUP,
            "race-resistant no-follow directory scanning is unavailable",
            str(path),
        )

    def _read_entries(self, directory: Path, iterator):
        entries = []
        errors = []
        with iterator:
            for entry in iterator:
                try:
                    entry_stat = self.stat_entry(directory, entry)
                except OSError as error:
                    errors.append((directory / entry.name, error))
                    continue
                entries.append((entry.name, entry_stat))
        entries.sort(key=lambda item: item[0])
        return entries, errors


def _require_same_directory(path: Path, expected_stat, actual_stat) -> None:
    expected_identity = (int(expected_stat.st_dev), int(expected_stat.st_ino))
    actual_identity = (int(actual_stat.st_dev), int(actual_stat.st_ino))
    if (
        expected_identity != actual_identity
        or not stat.S_ISDIR(actual_stat.st_mode)
        or _is_link_or_reparse(actual_stat)
    ):
        raise OSError(errno.ESTALE, "directory changed during scan", str(path))


def _directory_prefixes(path: Path) -> List[Path]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    return list(reversed(absolute.parents)) + [absolute]


def _open_directory_chain(path: Path):
    descriptors = []
    component_stats = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        for prefix in _directory_prefixes(path):
            if descriptors:
                descriptor = os.open(prefix.name, flags, dir_fd=descriptors[-1])
            else:
                descriptor = os.open(prefix, flags)
            descriptors.append(descriptor)
            component_stats.append(os.fstat(descriptor))
        _require_valid_chain(path, component_stats)
    except BaseException:
        _close_descriptors(descriptors)
        raise
    return descriptors, component_stats


def _close_descriptors(descriptors) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)


def _require_valid_chain(path: Path, component_stats) -> None:
    if not component_stats or any(
        not stat.S_ISDIR(component_stat.st_mode)
        or _is_link_or_reparse(component_stat)
        for component_stat in component_stats
    ):
        raise OSError(errno.ESTALE, "path contains a changed directory", str(path))


def _require_same_chain(path: Path, expected_stats, actual_stats) -> None:
    expected_identities = [
        (int(component_stat.st_dev), int(component_stat.st_ino))
        for component_stat in expected_stats
    ]
    actual_identities = [
        (int(component_stat.st_dev), int(component_stat.st_ino))
        for component_stat in actual_stats
    ]
    if expected_identities != actual_identities:
        raise OSError(errno.ESTALE, "path changed during scan", str(path))


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
    exclude: Optional[Callable[[Path], bool]] = None,
    allow_partial: bool = False,
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
    errors_encountered = 0
    pending = [(Path(root), None, None) for root in reversed(tuple(roots))]

    def report(current_path: Optional[Path], complete: bool = False) -> None:
        progress(
            ScanProgress(
                files_scanned=len(files),
                bytes_scanned=bytes_scanned,
                directories_scanned=directories_scanned,
                errors=errors_encountered,
                skipped_links=skipped_links,
                complete=complete,
                cancelled=cancel.is_set(),
                current_path=current_path,
            )
        )

    def record_error(path: Path, operation: str, error: OSError) -> None:
        nonlocal errors_encountered
        errors_encountered += 1
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
        path, root_device, expected_stat = pending.pop()
        if exclude is not None and exclude(path):
            report(path)
            continue
        if expected_stat is None:
            try:
                path_stat = filesystem.lstat(path)
            except OSError as error:
                record_error(path, "lstat", error)
                report(path)
                continue
        else:
            path_stat = expected_stat

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
        try:
            directory_stat, entries, entry_errors = filesystem.scan_directory(
                path, path_stat
            )
        except OSError as error:
            operation = (
                "unsupported-no-follow"
                if isinstance(error, UnsupportedNoFollowError)
                else "open-directory"
            )
            record_error(path, operation, error)
            report(path)
            continue
        directory_identity = (
            int(directory_stat.st_dev),
            int(directory_stat.st_ino),
        )
        if directory_identity in seen_directories:
            report(path)
            continue
        seen_directories.add(directory_identity)
        directories_scanned += 1
        for error_path, error in entry_errors:
            record_error(error_path, "lstat", error)
        for name, entry_stat in reversed(entries):
            pending.append((path / name, expected_device, entry_stat))
        report(path)

    completed_at = datetime.now(timezone.utc)
    complete = not cancel.is_set() and (errors_encountered == 0 or allow_partial)
    report(None, complete=complete)
    return ScanRun(
        run_id=uuid4().hex,
        platform=policy.platform,
        started_at=started_at,
        completed_at=completed_at,
        complete=complete,
        files=tuple(files),
        errors=tuple(errors),
        coverage={
            "status": "interrupted" if cancel.is_set() else ("partial" if errors_encountered else "complete"),
            "error_count": errors_encountered,
            "skipped_links": skipped_links,
            "files_scanned": len(files),
            "directories_scanned": directories_scanned,
        },
    )


__all__ = [
    "FileRecord",
    "FilesystemAdapter",
    "ScanError",
    "ScanProgress",
    "scan_paths",
]
