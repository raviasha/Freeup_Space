"""Exact duplicate analysis using size, partial fingerprint, then full hash."""

from __future__ import annotations

import hashlib
import os
import stat
from collections import defaultdict
from pathlib import Path
from typing import Callable, DefaultDict, Iterable, List, Optional, Tuple

from .models import DuplicateGroup, FileRecord
from .file_io import regular_descriptor


Hasher = Callable[[Path], str]
_PARTIAL_BYTES = 64 * 1024


class DuplicateAnalysisDeadlineExceeded(RuntimeError):
    """Raised when a bounded duplicate-analysis pass reaches its deadline."""


def _check_deadline(should_continue: Optional[Callable[[], bool]]) -> None:
    if should_continue is not None and not should_continue():
        raise DuplicateAnalysisDeadlineExceeded("duplicate analysis time budget exhausted")


def sha256_hasher(
    path: Path, *, should_continue: Optional[Callable[[], bool]] = None
) -> str:
    """Hash a regular file without following its final symlink component."""

    with regular_descriptor(path) as descriptor:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("duplicate hashing requires a regular file")
        digest = hashlib.sha256()
        while True:
            _check_deadline(should_continue)
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _matches_record(value, record: FileRecord) -> bool:
    return (
        stat.S_ISREG(value.st_mode)
        and int(value.st_dev) == record.st_dev
        and int(value.st_ino) == record.st_ino
        and int(value.st_size) == record.size
    )


def _partial_fingerprint(
    record: FileRecord, *, should_continue: Optional[Callable[[], bool]] = None
) -> Optional[str]:
    try:
        with regular_descriptor(record.path) as descriptor:
            _check_deadline(should_continue)
            before = os.fstat(descriptor)
            if not _matches_record(before, record):
                return None
            head = os.read(descriptor, _PARTIAL_BYTES)
            _check_deadline(should_continue)
            if record.size <= _PARTIAL_BYTES * 2:
                tail = os.read(descriptor, _PARTIAL_BYTES)
            else:
                os.lseek(descriptor, -_PARTIAL_BYTES, os.SEEK_END)
                tail = os.read(descriptor, _PARTIAL_BYTES)
            _check_deadline(should_continue)
            after = os.fstat(descriptor)
            if not _matches_record(after, record):
                return None
            if getattr(before, "st_mtime_ns", before.st_mtime) != getattr(
                after, "st_mtime_ns", after.st_mtime
            ):
                return None
    except OSError:
        return None
    digest = hashlib.sha256()
    digest.update(str(record.size).encode("ascii"))
    digest.update(b"\0")
    digest.update(head)
    digest.update(b"\0")
    digest.update(tail)
    return digest.hexdigest()


def _current_snapshot(record: FileRecord) -> Optional[Tuple[int, int, int, int, int]]:
    try:
        value = os.lstat(record.path)
    except OSError:
        return None
    if not _matches_record(value, record) or float(value.st_mtime) != record.mtime:
        return None
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", value.st_mtime * 1_000_000_000)),
        int(getattr(value, "st_ctime_ns", value.st_ctime * 1_000_000_000)),
    )


def find_duplicates(
    files: Iterable[FileRecord], hasher: Hasher, *, progress=None,
    should_continue: Optional[Callable[[], bool]] = None,
) -> List[DuplicateGroup]:
    """Confirm exact duplicates while excluding aliases of one physical file."""

    by_identity = {}
    for record in sorted(files, key=lambda item: str(item.path)):
        _check_deadline(should_continue)
        if record.file_kind != "regular" or record.size == 0:
            continue
        identity = (record.st_dev, record.st_ino)
        by_identity.setdefault(identity, record)

    by_size: DefaultDict[int, List[FileRecord]] = defaultdict(list)
    for record in by_identity.values():
        by_size[record.size].append(record)

    groups: List[DuplicateGroup] = []
    for size in sorted(by_size):
        _check_deadline(should_continue)
        size_group = sorted(by_size[size], key=lambda item: str(item.path))
        if len(size_group) < 2:
            continue
        by_partial: DefaultDict[str, List[FileRecord]] = defaultdict(list)
        for record in size_group:
            _check_deadline(should_continue)
            fingerprint = _partial_fingerprint(record, should_continue=should_continue)
            if fingerprint is not None:
                by_partial[fingerprint].append(record)
            if progress is not None:
                progress(record.path)
        for fingerprint in sorted(by_partial):
            _check_deadline(should_continue)
            partial_group = by_partial[fingerprint]
            if len(partial_group) < 2:
                continue
            by_digest: DefaultDict[str, List[FileRecord]] = defaultdict(list)
            for record in partial_group:
                _check_deadline(should_continue)
                before_hash = _current_snapshot(record)
                if before_hash is None:
                    continue
                try:
                    if hasher is sha256_hasher:
                        claimed_digest = sha256_hasher(
                            record.path, should_continue=should_continue
                        )
                        digest = claimed_digest
                    else:
                        claimed_digest = hasher(record.path)
                        digest = sha256_hasher(
                            record.path, should_continue=should_continue
                        )
                except (OSError, ValueError):
                    continue
                if (
                    isinstance(claimed_digest, str)
                    and claimed_digest == digest
                    and _current_snapshot(record) == before_hash
                ):
                    by_digest[digest].append(record)
                if progress is not None:
                    progress(record.path)
            for digest in sorted(by_digest):
                exact = sorted(by_digest[digest], key=lambda item: str(item.path))
                if len(exact) < 2:
                    continue
                retained = exact[0]
                duplicates = tuple(item.path for item in exact[1:])
                group_token = hashlib.sha256(
                    "{}:{}".format(size, digest).encode("utf-8")
                ).hexdigest()[:12].upper()
                groups.append(
                    DuplicateGroup(
                        group_id="DUP-{}".format(group_token),
                        digest=digest,
                        size=size,
                        retained_path=retained.path,
                        duplicate_paths=duplicates,
                        reclaimable_bytes=size * len(duplicates),
                    )
                )
    groups.sort(key=lambda group: (str(group.retained_path), group.digest))
    return groups
