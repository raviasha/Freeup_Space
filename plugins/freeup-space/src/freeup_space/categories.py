"""Additive, policy-owned category evidence for scanned regular files."""

from __future__ import annotations

import ntpath
import posixpath
from dataclasses import replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import Evidence, EvidenceCandidate, FileRecord, Risk
from .policy import Policy
from .safety import is_protected
from .storage_context import storage_context
from .path_utils import normalize_path


_RISK_ORDER = {
    Risk.LOW: 0,
    Risk.MEDIUM: 1,
    Risk.HIGH: 2,
    Risk.REPORT_ONLY: 3,
}
_PERSONAL_EXTENSIONS = frozenset(
    {
        ".doc",
        ".docx",
        ".heic",
        ".jpeg",
        ".jpg",
        ".m4a",
        ".mov",
        ".mp3",
        ".mp4",
        ".pages",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".raw",
        ".wav",
        ".xls",
        ".xlsx",
    }
)
_ARCHIVE_EXTENSIONS = frozenset(
    {".7z", ".bz2", ".dmg", ".gz", ".iso", ".rar", ".tar", ".tgz", ".xz", ".zip"}
)
_INSTALLER_EXTENSIONS = frozenset({".exe", ".msi", ".pkg"})
_DATABASE_EXTENSIONS = frozenset({".db", ".db3", ".sqlite", ".sqlite3"})


def _normalize(value: str, platform: str) -> str:
    return normalize_path(value, platform)


def _inside(path: Path, root: str, platform: str) -> bool:
    path_module = ntpath if platform == "windows" else posixpath
    candidate = _normalize(str(path), platform)
    safe_root = _normalize(root, platform)
    try:
        return path_module.commonpath((candidate, safe_root)) == safe_root
    except ValueError:
        return False


def _safe_root(path: Path, policy: Policy) -> Optional[str]:
    matches = [
        root for root in policy.safe_roots if _inside(path, root, policy.platform)
    ]
    if not matches:
        return None
    return max(matches, key=lambda value: len(_normalize(value, policy.platform)))


def _root_kind(root: str, platform: str) -> Optional[str]:
    normalized = _normalize(root, platform).rstrip("/\\")
    basename = (ntpath if platform == "windows" else posixpath).basename(normalized)
    name = basename.casefold()
    if name in {".cache", "cache", "caches"}:
        return "cache"
    if name in {"log", "logs"}:
        return "log"
    if name in {"temp", "tmp"}:
        return "temporary"
    if name == "downloads":
        return "download"
    return None


def _evidence(
    record: FileRecord,
    policy: Policy,
    category: str,
    rule: str,
    reason: Optional[str] = None,
    **details: object,
) -> Evidence:
    category_rule = policy.category_rules[category]
    evidence_size = (
        record.allocated_size
        if record.file_kind == "directory" and record.allocated_size is not None
        else record.size
    )
    return Evidence(
        path=record.path,
        category=category,
        rule=rule,
        reason=reason or category_rule.reason,
        risk=category_rule.risk,
        actionable=category_rule.actionable,
        size=evidence_size,
        details=details,
    )


def classify(record: FileRecord, policy: Policy) -> List[Evidence]:
    """Return every supported reason, ordered with the highest risk first.

    Protected and rejected paths are deliberately an override: lower-risk filename
    or location heuristics never make OS-managed data actionable.
    """

    findings: List[Evidence] = storage_context(record, policy)
    protection = is_protected(record.path, policy, policy.platform)
    if not protection.actionable:
        category = (
            "system-managed"
            if protection.outcome == "report-only"
            else "unclassified"
        )
        rule = (
            "protected-path"
            if protection.outcome == "report-only"
            else "rejected-path"
        )
        findings.append(_evidence(record, policy, category, rule, protection.reason))

    safe_root = _safe_root(record.path, policy)
    root_kind = _root_kind(safe_root, policy.platform) if safe_root else None
    if root_kind is not None:
        rule = (
            "download-location"
            if root_kind == "download"
            else "known-{}-root".format(root_kind)
        )
        findings.append(
            _evidence(
                record,
                policy,
                root_kind,
                rule,
                safe_root=safe_root,
            )
        )

    suffix = record.path.suffix.casefold()
    # A log suffix is low-risk evidence only under a policy-recognized safe root.
    if suffix == ".log" and safe_root is not None and root_kind != "log":
        findings.append(
            _evidence(record, policy, "log", "log-extension", safe_root=safe_root)
        )
    if suffix in _ARCHIVE_EXTENSIONS:
        findings.append(_evidence(record, policy, "archive", "archive-extension"))
    if suffix in _INSTALLER_EXTENSIONS:
        findings.append(_evidence(record, policy, "installer", "installer-extension"))
    if suffix in _PERSONAL_EXTENSIONS:
        findings.append(
            _evidence(record, policy, "personal-data", "personal-media-extension")
        )
    if suffix in _DATABASE_EXTENSIONS:
        findings.append(
            _evidence(
                record,
                policy,
                "system-managed",
                "database-extension",
                "database consistency is unknown",
            )
        )

    components = {part.casefold() for part in record.path.parts}
    if components.intersection({"deriveddata", "node_modules", "build", "dist"}):
        findings.append(
            _evidence(record, policy, "developer-artifact", "developer-build-location")
        )
    if components.intersection({"backup", "backups", "mobilebackups"}):
        findings.append(_evidence(record, policy, "backup", "backup-location"))
    if ".trash" in components or "$recycle.bin" in components:
        findings.append(
            _evidence(
                record,
                policy,
                "system-managed",
                "existing-trash-content",
                "existing Trash or Recycle Bin content",
            )
        )

    if not findings:
        findings.append(_evidence(record, policy, "unclassified", "unclassified"))
    highest_risk = max(findings, key=lambda item: _RISK_ORDER[item.risk]).risk
    actionable = all(item.actionable for item in findings)
    findings = [
        replace(
            item,
            risk=highest_risk,
            actionable=actionable,
            details=dict(item.details, rule_risk=item.risk.value),
        )
        for item in findings
    ]
    findings.sort(key=lambda item: (-_RISK_ORDER[item.risk], item.rule))
    return findings


def reconcile_evidence(
    evidence: Iterable[Evidence], policy: Policy
) -> List[EvidenceCandidate]:
    """Collapse additive evidence to one policy-safe candidate per lexical path."""

    grouped: Dict[str, List[Evidence]] = {}
    for item in evidence:
        key = _normalize(str(item.path), policy.platform)
        grouped.setdefault(key, []).append(item)

    candidates = []
    for key in sorted(grouped):
        items = sorted(grouped[key], key=lambda item: (item.rule, item.reason))
        path = min((item.path for item in items), key=str)
        sizes = {item.size for item in items}
        consistent_size = len(sizes) == 1
        size = next(iter(sizes)) if consistent_size else 0
        fallback_rule = policy.category_rules["unclassified"]
        category_rules = [
            policy.category_rules.get(item.category, fallback_rule) for item in items
        ]
        risk = max(
            [item.risk for item in items]
            + [category_rule.risk for category_rule in category_rules],
            key=lambda value: _RISK_ORDER[value],
        )
        actionable = (
            consistent_size
            and all(item.actionable for item in items)
            and all(category_rule.actionable for category_rule in category_rules)
        )
        protection = is_protected(path, policy, policy.platform)
        if not protection.actionable or not consistent_size:
            risk = Risk.REPORT_ONLY
            actionable = False
        candidates.append(
            EvidenceCandidate(
                path=path,
                categories=tuple(sorted({item.category for item in items})),
                rules=tuple(item.rule for item in items),
                reasons=tuple(item.reason for item in items),
                risk=risk,
                actionable=actionable,
                size=size,
                reclaimable_bytes=size if actionable else 0,
                evidence=tuple(items),
            )
        )
    return candidates
