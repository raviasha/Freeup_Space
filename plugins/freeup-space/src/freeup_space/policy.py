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
                    "/private/var/db",
                    "/private/var/vm",
                    "/private/var/root",
                    "/Volumes/.timemachine",
                    "/Volumes/MobileBackups",
                ),
                protected_files=("/private/var/vm/sleepimage",),
                category_rules=_CATEGORY_RULES,
                workspace_roots=(str(Path.cwd()),),
            )
        if normalized == "windows":
            return cls(
                platform=normalized,
                protected_roots=(
                    r"C:\Windows",
                    r"C:\Program Files",
                    r"C:\Program Files (x86)",
                    r"C:\ProgramData",
                    r"C:\System Volume Information",
                    r"C:\Recovery",
                    r"C:\$Recycle.Bin",
                    r"C:\Windows.old",
                ),
                protected_files=(
                    r"C:\hiberfil.sys",
                    r"C:\pagefile.sys",
                    r"C:\swapfile.sys",
                ),
                category_rules=_CATEGORY_RULES,
                workspace_roots=(str(Path.cwd()),),
            )
        raise ValueError(
            "Unsupported platform {!r}; expected 'macos' or 'windows'".format(platform)
        )
