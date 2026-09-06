from pathlib import Path

import pytest

from freeup_space import volumes


def test_macos_enumeration_keeps_local_volumes_and_excludes_network_mounts(
    monkeypatch,
):
    mount_output = "\n".join(
        (
            "/dev/disk3s1s1 on / (apfs, local, read-only, journaled)",
            "/dev/disk7s2 on /Volumes/Media\\040Drive (exfat, local, nodev)",
            "server:/archive on /Volumes/Archive (nfs, nodev)",
            "//user@server/share on /Volumes/Share (smbfs, nodev)",
        )
    )
    monkeypatch.setattr(volumes, "_mount_output", lambda: mount_output)
    monkeypatch.setattr(
        volumes,
        "_diskutil_info",
        lambda path: {
            "VolumeName": "Macintosh HD" if path == Path("/") else "Media Drive",
            "Internal": path == Path("/"),
            "RemovableMedia": path != Path("/"),
            "VolumeUUID": "root-id" if path == Path("/") else "media-id",
        },
    )

    found = volumes.enumerate_local_volumes("darwin")

    assert [(item.path, item.kind) for item in found] == [
        (Path("/"), "fixed"),
        (Path("/Volumes/Media Drive"), "removable"),
    ]
    assert [item.volume_id for item in found] == ["root-id", "media-id"]


def test_macos_enumeration_excludes_non_device_and_duplicate_mount_aliases(monkeypatch):
    monkeypatch.setattr(
        volumes,
        "_mount_output",
        lambda: "\n".join(
            (
                "/dev/disk3s1 on / (apfs, local)",
                "/dev/disk3s1 on /System/Volumes/Data (apfs, local)",
                "map auto_home on /System/Volumes/Data/home (autofs, automounted)",
            )
        ),
    )
    monkeypatch.setattr(volumes, "_diskutil_info", lambda path: {})

    found = volumes.enumerate_local_volumes("macos")

    assert [item.path for item in found] == [Path("/")]


def test_windows_enumeration_returns_only_fixed_and_removable_drives(monkeypatch):
    # A:, C:, and Z: are present; Z: is a network drive.
    monkeypatch.setattr(
        volumes,
        "_windows_volume_api",
        lambda: (lambda: (1 << 0) | (1 << 2) | (1 << 25), lambda root: {
            "A:\\": 2,
            "C:\\": 3,
            "Z:\\": 4,
        }[root]),
    )

    found = volumes.enumerate_local_volumes("win32")

    assert [(str(item.path), item.kind) for item in found] == [
        ("A:\\", "removable"),
        ("C:\\", "fixed"),
    ]


def test_volume_enumeration_rejects_unsupported_platform():
    with pytest.raises(ValueError, match="Unsupported platform"):
        volumes.enumerate_local_volumes("linux")
