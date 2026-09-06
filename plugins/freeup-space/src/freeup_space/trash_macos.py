"""Native recoverable deletion helpers for macOS."""

from __future__ import annotations

import subprocess
from pathlib import Path


def move_to_trash(path: Path, platform: str) -> bool:
    """Move a path to the user's Trash using Finder on macOS."""

    if platform != "macos":
        raise ValueError("macOS trash adapter received {!r}".format(platform))
    script = (
        'tell application "Finder" to delete POSIX file "{}"'.format(
            str(path).replace('"', '\\"')
        )
    )
    subprocess.run(["osascript", "-e", script], check=True)
    return True


def empty_trash() -> None:
    """Intentionally unused in this project."""

    raise RuntimeError("empty_trash must never be called")

