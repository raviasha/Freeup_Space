"""Enumerate local fixed and removable volumes on macOS and Windows."""

from __future__ import annotations

import ctypes
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from .models import Volume


_NETWORK_FILESYSTEMS = frozenset(
    {
        "afpfs",
        "cifs",
        "nfs",
        "nfs4",
        "smbfs",
        "sshfs",
        "webdav",
    }
)
_MOUNT_PATTERN = re.compile(r"^(?P<device>.+?) on (?P<path>.+?) \((?P<options>[^)]*)\)$")


def _normalize_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    if normalized == "darwin":
        return "macos"
    if normalized == "win32":
        return "windows"
    if normalized not in ("macos", "windows"):
        raise ValueError(
            "Unsupported platform {!r}; expected 'macos' or 'windows'".format(
                platform
            )
        )
    return normalized


def _mount_output() -> str:
    completed = subprocess.run(
        ["mount"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def _diskutil_info(path: Path) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            ["diskutil", "info", "-plist", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        value = plistlib.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, plistlib.InvalidFileException):
        return {}
    return value if isinstance(value, dict) else {}


def _unescape_mount_path(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _macos_volumes() -> List[Volume]:
    volumes = []
    seen_devices = set()
    seen_paths = set()
    for line in _mount_output().splitlines():
        match = _MOUNT_PATTERN.match(line.strip())
        if match is None:
            continue
        device = match.group("device")
        path = Path(_unescape_mount_path(match.group("path")))
        options = tuple(
            option.strip().lower() for option in match.group("options").split(",")
        )
        filesystem = options[0] if options else None
        if not device.startswith("/dev/"):
            continue
        if filesystem in _NETWORK_FILESYSTEMS:
            continue
        if path != Path("/") and path.parent != Path("/Volumes"):
            continue
        if device in seen_devices or path in seen_paths:
            continue
        info = _diskutil_info(path)
        removable = bool(info.get("RemovableMedia", False))
        name = info.get("VolumeName") or path.name or "/"
        volume_id = info.get("VolumeUUID") or info.get("DiskUUID") or device
        volumes.append(
            Volume(
                path=path,
                name=str(name),
                kind="removable" if removable else "fixed",
                filesystem=filesystem,
                device=device,
                volume_id=str(volume_id),
            )
        )
        seen_devices.add(device)
        seen_paths.add(path)
    volumes.sort(key=lambda volume: (volume.path != Path("/"), str(volume.path)))
    return volumes


def _windows_volume_api() -> Tuple[Callable[[], int], Callable[[str], int]]:
    kernel32 = ctypes.windll.kernel32
    get_logical_drives = kernel32.GetLogicalDrives
    get_logical_drives.restype = ctypes.c_uint32
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = [ctypes.c_wchar_p]
    get_drive_type.restype = ctypes.c_uint
    return get_logical_drives, get_drive_type


def _windows_volumes() -> List[Volume]:
    get_logical_drives, get_drive_type = _windows_volume_api()
    mask = get_logical_drives()
    if mask == 0:
        raise OSError("GetLogicalDrives failed")
    volumes = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = "{}:\\".format(chr(ord("A") + index))
        drive_type = get_drive_type(root)
        if drive_type not in (2, 3):
            continue
        volumes.append(
            Volume(
                path=Path(root),
                name=root,
                kind="removable" if drive_type == 2 else "fixed",
                device=root,
                volume_id=root,
            )
        )
    return volumes


def enumerate_local_volumes(platform: str) -> List[Volume]:
    """Return local fixed/removable volumes, excluding remote filesystems."""

    normalized = _normalize_platform(platform)
    return _macos_volumes() if normalized == "macos" else _windows_volumes()


__all__ = ["Volume", "enumerate_local_volumes"]
