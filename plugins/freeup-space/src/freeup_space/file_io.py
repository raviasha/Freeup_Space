"""Content reads with no-follow checks on every path component."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def regular_descriptor(path: Path):
    path = Path(os.path.abspath(path))
    if os.name == "nt":
        from .windows_fs import regular_descriptor as windows_descriptor
        with windows_descriptor(path) as descriptor:
            yield descriptor
        return
    from .scanner import _open_directory_chain, _close_descriptors
    parents, _ = _open_directory_chain(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parents[-1])
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("content reads require a regular file")
            yield descriptor
        finally:
            os.close(descriptor)
    finally:
        _close_descriptors(parents)
