"""Platform-specific cleanup policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Tuple

from .models import Risk


@dataclass(frozen=True)
class CategoryRule:
    risk: Risk
    actionable: bool
    reason: str


_CATEGORY_RULES = MappingProxyType(
    {
        "cache": CategoryRule(Risk.LOW, True, "reproducible cache data"),
        "temporary": CategoryRule(Risk.LOW, True, "temporary data"),
        "log": CategoryRule(Risk.LOW, True, "diagnostic log data"),
        "duplicate": CategoryRule(Risk.MEDIUM, True, "confirmed duplicate"),
        "old-file": CategoryRule(Risk.MEDIUM, True, "old user or tool data"),
        "download": CategoryRule(Risk.MEDIUM, True, "downloaded user data"),
        "archive": CategoryRule(Risk.MEDIUM, True, "archive or disk image"),
        "installer": CategoryRule(Risk.MEDIUM, True, "downloaded installer"),
        "developer-artifact": CategoryRule(
            Risk.MEDIUM, True, "reproducible developer artifact"
        ),
        "personal-data": CategoryRule(Risk.HIGH, True, "valuable user data"),
        "system-managed": CategoryRule(
            Risk.REPORT_ONLY, False, "operating-system managed data"
        ),
        "application": CategoryRule(
            Risk.REPORT_ONLY, False, "installed application data"
        ),
        "backup": CategoryRule(Risk.REPORT_ONLY, False, "backup or recovery data"),
        "unclassified": CategoryRule(
            Risk.REPORT_ONLY, False, "insufficient evidence for safe cleanup"
        ),
        "managed-storage": CategoryRule(Risk.REPORT_ONLY, False, "use the owning application's storage manager"),
    }
)


@dataclass(frozen=True)
class Policy:
    platform: str
    protected_roots: Tuple[str, ...]
    protected_files: Tuple[str, ...]
    category_rules: Mapping[str, CategoryRule]
    version: str = "1"
    workspace_roots: Tuple[str, ...] = ()
    safe_roots: Tuple[str, ...] = ()

    @classmethod
    def for_platform(cls, platform: str) -> "Policy":
        normalized = platform.strip().lower()
        if normalized == "darwin":
            normalized = "macos"
        elif normalized == "win32":
            normalized = "windows"

        if normalized == "macos":
            return cls(
                platform=normalized,
                protected_roots=(
                    "/System",
                    "/Library",
                    "/Applications",
                    "/usr",
                    "/bin",
                    "/sbin",
                    "/dev",
                    "/private/etc",
                    "/private/var/db",
                    "/private/var/log",
                    "/private/var/vm",
                    "/private/var/root",
                    "/Volumes/.timemachine",
                    "/Volumes/MobileBackups",
                ),
                protected_files=("/private/var/vm/sleepimage",),
                category_rules=_CATEGORY_RULES,
                workspace_roots=(str(Path.cwd()),),
                safe_roots=(
                    str(Path.home() / "Library" / "Caches"),
                    str(Path.home() / "Library" / "Logs"),
                    str(Path.home() / ".cache"),
                    str(Path.home() / "Downloads"),
                    str(Path.home() / "Documents"),
                    str(Path.home() / "Desktop"),
                ),
            )
        if normalized == "windows":
            return cls(
                platform=normalized,
                protected_roots=(
                    r"\Windows",
                    r"\Program Files",
                    r"\Program Files (x86)",
                    r"\ProgramData",
                    r"\System Volume Information",
                    r"\Recovery",
                    r"\$Recycle.Bin",
                    r"\Windows.old",
                ),
                protected_files=(
                    r"\hiberfil.sys",
                    r"\pagefile.sys",
                    r"\swapfile.sys",
                ),
                category_rules=_CATEGORY_RULES,
                workspace_roots=(str(Path.cwd()),),
                safe_roots=(
                    str(Path.home() / "AppData" / "Local" / "Temp"),
                    str(Path.home() / ".cache"),
                    str(Path.home() / "Downloads"),
                    str(Path.home() / "Documents"),
                    str(Path.home() / "Desktop"),
                ),
            )
        raise ValueError(
            "Unsupported platform {!r}; expected 'macos' or 'windows'".format(platform)
        )
