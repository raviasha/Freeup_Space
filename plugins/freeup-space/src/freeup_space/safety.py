"""Protected-path checks and pre-action filesystem validation."""

from __future__ import annotations

import hashlib
import ntpath
import os
import posixpath
import stat
from pathlib import Path
from typing import Callable, Optional

from .models import CandidateSnapshot, SafetyDecision
from .policy import Policy
from .path_utils import normalize_path


def _platform_name(platform: str) -> str:
    name = platform.strip().lower()
    if name == "darwin":
        return "macos"
    if name == "win32":
        return "windows"
    return name


def _normalizer(platform: str) -> Callable[[str], str]:
    return lambda value: normalize_path(value, _platform_name(platform))


def _contains(path: str, root: str, platform: str) -> bool:
    normalize = _normalizer(platform)
    path_value = normalize(path)
    root_value = normalize(root)
    path_module = ntpath if _platform_name(platform) == "windows" else posixpath
    try:
        return path_module.commonpath((path_value, root_value)) == root_value
    except ValueError:
        return False


def _is_windows_device_namespace(path: str) -> bool:
    normalized_separators = path.replace("/", "\\").casefold()
    return normalized_separators.startswith(
        ("\\\\?\\", "\\\\.\\", "\\??\\", "\\\\??\\", "\\device\\")
    )


def is_protected(path: Path, policy: Policy, platform: str) -> SafetyDecision:
    """Classify a path using lexical normalization, never link resolution."""

    requested_platform = _platform_name(platform)
    if requested_platform != policy.platform:
        return SafetyDecision(False, "reject", "platform does not match safety policy")

    path_text = str(path)
    if requested_platform == "windows" and _is_windows_device_namespace(path_text):
        return SafetyDecision(
            False, "reject", "Win32 device and extended path namespaces are not allowed"
        )
    normalize = _normalizer(requested_platform)
    normalized = normalize(path_text)
    if requested_platform == "windows":
        drive, tail = ntpath.splitdrive(normalized)
        if drive and tail in ("", "\\"):
            return SafetyDecision(False, "reject", "filesystem roots are never actionable")
        if drive.startswith("\\\\"):
            return SafetyDecision(False, "reject", "UNC paths require a separate policy")
        if not drive or not tail.startswith("\\"):
            return SafetyDecision(False, "reject", "Windows target must be drive-absolute")
    elif normalized == "/":
        return SafetyDecision(False, "reject", "filesystem roots are never actionable")
    for protected_file in policy.protected_files:
        candidate = tail if requested_platform == "windows" else normalized
        if candidate == normalize(protected_file):
            return SafetyDecision(False, "report-only", "path is an OS-managed file")
    for protected_root in policy.protected_roots:
        candidate = tail if requested_platform == "windows" else normalized
        if _contains(candidate, protected_root, requested_platform):
            return SafetyDecision(
                False,
                "report-only",
                "path is within protected root {}".format(protected_root),
            )
    return SafetyDecision(True, "allow", "path is not protected by platform policy")


def _is_filesystem_root(path: Path) -> bool:
    absolute = Path(os.path.abspath(os.fspath(path)))
    return absolute.parent == absolute


def _has_link_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(os.fspath(path)))
    components = (absolute,) + tuple(absolute.parents)
    for component in reversed(components):
        try:
            component_stat = component.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(component_stat.st_mode):
            return True
        if getattr(component_stat, "st_file_attributes", 0) & 0x400:
            return True
    return False


def _digest(path: Path) -> str:
    from .duplicates import sha256_hasher
    return sha256_hasher(path)


def _is_recognized_safe_root(path: Path, policy: Policy) -> bool:
    normalize = _normalizer(policy.platform)
    normalized = normalize(str(path))
    return any(normalized == normalize(root) for root in policy.safe_roots)


def _unsafe_directory_content(
    path: Path, policy: Policy, expected_device: int
) -> Optional[str]:
    pending = [path]
    while pending:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except OSError as error:
            return "directory content cannot be inspected: {}".format(error)
        with entries:
            for entry in entries:
                entry_path = Path(entry.path)
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError as error:
                    return "directory content cannot be inspected: {}".format(error)
                if stat.S_ISLNK(entry_stat.st_mode) or (
                    getattr(entry_stat, "st_file_attributes", 0) & 0x400
                ):
                    return "directory contains a link or reparse point"
                if entry_stat.st_dev != expected_device:
                    return "directory contains content on an unexpected filesystem"
                protected = is_protected(entry_path, policy, policy.platform)
                if not protected.actionable:
                    return "directory contains protected content: {}".format(
                        protected.reason
                    )
                if stat.S_ISDIR(entry_stat.st_mode):
                    pending.append(entry_path)
    return None


def validate_target(
    path: Path, policy: Policy, snapshot: CandidateSnapshot
) -> SafetyDecision:
    """Reject a target unless it is unchanged and remains lexically contained."""

    if _is_filesystem_root(path):
        return SafetyDecision(False, "reject", "filesystem roots are never actionable")

    absolute = Path(os.path.abspath(os.fspath(path)))
    home = Path.home()
    if absolute == home:
        return SafetyDecision(False, "reject", "the home directory is never actionable")

    normalize = _normalizer(policy.platform)
    for workspace_root in policy.workspace_roots:
        if normalize(str(path)) == normalize(workspace_root):
            return SafetyDecision(False, "reject", "a workspace root is never actionable")

    protected = is_protected(path, policy, policy.platform)
    if not protected.actionable:
        return SafetyDecision(False, "reject", protected.reason)

    if snapshot.path is not None:
        if normalize(str(path)) != normalize(str(snapshot.path)):
            return SafetyDecision(False, "reject", "target differs from snapshot path")

    if snapshot.safe_root is not None and not _contains(
        str(path), str(snapshot.safe_root), policy.platform
    ):
        return SafetyDecision(False, "reject", "target is outside its recorded safe root")

    try:
        current = path.lstat()
    except FileNotFoundError:
        return SafetyDecision(False, "reject", "target is missing")
    except OSError as error:
        return SafetyDecision(False, "reject", "target cannot be inspected: {}".format(error))

    if _has_link_component(path):
        return SafetyDecision(False, "reject", "target or a parent is a link or reparse point")

    if stat.S_ISDIR(current.st_mode):
        if snapshot.safe_root is None:
            return SafetyDecision(
                False, "reject", "directory candidates require a recognized safe root"
            )
        if not _is_recognized_safe_root(snapshot.safe_root, policy):
            return SafetyDecision(
                False, "reject", "directory safe root is not recognized by policy"
            )
        try:
            safe_root_stat = snapshot.safe_root.lstat()
        except FileNotFoundError:
            return SafetyDecision(False, "reject", "directory safe root is missing")
        except OSError as error:
            return SafetyDecision(
                False,
                "reject",
                "directory safe root cannot be inspected: {}".format(error),
            )
        if current.st_dev != safe_root_stat.st_dev:
            return SafetyDecision(
                False,
                "reject",
                "directory is on a different filesystem from its safe root",
            )
        normalize = _normalizer(policy.platform)
        if normalize(str(path)) == normalize(str(snapshot.safe_root)):
            return SafetyDecision(
                False, "reject", "a recognized safe root cannot itself be removed"
            )
        unsafe_content = _unsafe_directory_content(
            path, policy, safe_root_stat.st_dev
        )
        if unsafe_content is not None:
            return SafetyDecision(False, "reject", unsafe_content)

    if (
        current.st_dev != snapshot.st_dev
        or current.st_ino != snapshot.st_ino
        or current.st_size != snapshot.size
        or current.st_mtime != snapshot.mtime
    ):
        return SafetyDecision(False, "reject", "target snapshot no longer matches")

    if snapshot.digest is not None:
        if not stat.S_ISREG(current.st_mode):
            return SafetyDecision(False, "reject", "digest cannot validate a non-regular file")
        try:
            current_digest = _digest(path)
        except OSError as error:
            return SafetyDecision(False, "reject", "target cannot be hashed: {}".format(error))
        if current_digest != snapshot.digest:
            return SafetyDecision(False, "reject", "target digest no longer matches")

    return SafetyDecision(True, "allow", "target matches its recorded safe snapshot")


__all__ = ["SafetyDecision", "is_protected", "validate_target"]
