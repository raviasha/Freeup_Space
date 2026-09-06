"""Deterministic, immutable cleanup-plan construction and explicit selection."""

from __future__ import annotations

import hashlib
import json
import ntpath
import posixpath
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Tuple

from .categories import reconcile_evidence
from .models import (
    ActionType,
    Candidate,
    CandidateSnapshot,
    CleanupPlan,
    Evidence,
    FileRecord,
    Risk,
    ScanRun,
)
from .policy import Policy
from .path_utils import normalize_path


SCHEMA_VERSION = "1"
_RISK_ORDER = {
    Risk.LOW: 0,
    Risk.MEDIUM: 1,
    Risk.HIGH: 2,
    Risk.REPORT_ONLY: 3,
}
_ID_PREFIXES = {
    "application": "APP",
    "archive": "ARC",
    "backup": "BAK",
    "cache": "CAC",
    "developer-artifact": "DEV",
    "download": "DWN",
    "duplicate": "DUP",
    "installer": "INS",
    "log": "LOG",
    "old-file": "OLD",
    "personal-data": "PER",
    "system-managed": "SYS",
    "temporary": "TMP",
    "unclassified": "UNC",
}


class PlanError(ValueError):
    """A scan cannot form a safe plan, or an explicit selection is invalid."""


@dataclass(frozen=True)
class Selection:
    """The exact actionable candidates explicitly named for one plan."""

    candidate_ids: Tuple[str, ...]
    candidates: Tuple[Candidate, ...]
    reclaimable_bytes: int
    plan_digest: str
    action: ActionType


def _normalized_path(path: Path, platform: str) -> str:
    return normalize_path(str(path), platform)


def _safe_root(path: Path, policy: Policy) -> Optional[Path]:
    path_module = ntpath if policy.platform == "windows" else posixpath
    candidate = _normalized_path(path, policy.platform)
    matches = []
    for root in policy.safe_roots:
        normalized_root = _normalized_path(Path(root), policy.platform)
        try:
            if path_module.commonpath((candidate, normalized_root)) == normalized_root:
                matches.append((len(normalized_root), Path(root)))
        except ValueError:
            continue
    return max(matches, default=(0, None), key=lambda item: item[0])[1]


def _duplicate_evidence(
    run: ScanRun, records: Mapping[str, FileRecord]
) -> Tuple[Evidence, ...]:
    findings = []
    for group in run.duplicate_groups:
        retained_key = _normalized_path(group.retained_path, run.platform)
        duplicate_keys = tuple(
            _normalized_path(path, run.platform) for path in group.duplicate_paths
        )
        retained_record = records.get(retained_key)
        try:
            digest_is_sha256 = (
                isinstance(group.digest, str)
                and len(group.digest) == 64
                and int(group.digest, 16) >= 0
            )
        except ValueError:
            digest_is_sha256 = False
        group_records = [records.get(key) for key in duplicate_keys]
        identities = (
            [(retained_record.st_dev, retained_record.st_ino)]
            if retained_record is not None
            else []
        ) + [
            (record.st_dev, record.st_ino)
            for record in group_records
            if record is not None
        ]
        if (
            retained_record is None
            or not duplicate_keys
            or retained_key in duplicate_keys
            or len(set(duplicate_keys)) != len(duplicate_keys)
            or any(record is None for record in group_records)
            or retained_record.size != group.size
            or any(record.size != group.size for record in group_records)
            or len(set(identities)) != len(identities)
            or not digest_is_sha256
            or group.reclaimable_bytes != group.size * len(duplicate_keys)
        ):
            raise PlanError("invalid duplicate group: {}".format(group.group_id))
        for path in group.duplicate_paths:
            record = records.get(_normalized_path(path, run.platform))
            assert record is not None
            findings.append(
                Evidence(
                    path=path,
                    category="duplicate",
                    rule="exact-duplicate",
                    reason="exact duplicate; one copy is retained",
                    risk=Risk.MEDIUM,
                    actionable=True,
                    size=record.size,
                    details={
                        "duplicate_group": group.group_id,
                        "digest": group.digest,
                        "retained_path": group.retained_path,
                    },
                )
            )
    return tuple(findings)


def _primary_category(categories: Tuple[str, ...], duplicate: bool, risk: Risk) -> str:
    if risk is Risk.REPORT_ONLY and "system-managed" in categories:
        return "system-managed"
    if duplicate:
        return "duplicate"
    return min(categories) if categories else "unclassified"


def _candidate_sort_key(candidate: Candidate):
    return (
        _RISK_ORDER[candidate.risk],
        -candidate.reclaimable_bytes,
        candidate.category,
        str(candidate.path),
    )


def _digest(plan: CleanupPlan) -> str:
    payload = plan.to_dict()
    payload["digest"] = ""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_plan(
    run: ScanRun,
    evidence: Iterable[Evidence],
    policy: Policy,
    action: ActionType = ActionType.TRASH,
) -> CleanupPlan:
    """Build a deterministic plan from a completed inventory and additive evidence."""

    if not run.complete:
        raise PlanError("cleanup plans require a complete scan")
    if run.platform != policy.platform:
        raise PlanError("scan platform and policy platform do not match")
    if not isinstance(action, ActionType):
        raise PlanError("plan action must be an ActionType")

    records = {
        _normalized_path(record.path, run.platform): record
        for record in run.files
        if isinstance(record, FileRecord)
    }
    duplicate_findings = _duplicate_evidence(run, records)
    reconciled = reconcile_evidence(tuple(evidence) + duplicate_findings, policy)
    duplicate_by_path = {
        _normalized_path(path, run.platform): group
        for group in run.duplicate_groups
        for path in group.duplicate_paths
    }

    candidates = []
    for finding in reconciled:
        key = _normalized_path(finding.path, run.platform)
        record = records.get(key)
        if record is None:
            raise PlanError("candidate is absent from the scan inventory: {}".format(finding.path))
        duplicate_group = duplicate_by_path.get(key)
        category = _primary_category(
            finding.categories, duplicate_group is not None, finding.risk
        )
        evidence_data = {
            "categories": finding.categories,
            "rules": finding.rules,
        }
        digest = None
        if duplicate_group is not None:
            digest = duplicate_group.digest
            keeper = records[_normalized_path(duplicate_group.retained_path, run.platform)]
            evidence_data.update(
                {
                    "duplicate_group": duplicate_group.group_id,
                    "retained_path": str(duplicate_group.retained_path),
                    "retained_snapshot": {"st_dev": keeper.st_dev, "st_ino": keeper.st_ino,
                                          "size": keeper.size, "mtime": keeper.mtime},
                }
            )
        candidates.append(
            Candidate(
                candidate_id="",
                path=finding.path,
                display_path=str(finding.path),
                volume_id=record.volume_id,
                file_id=record.file_id,
                size=record.size,
                allocated_size=record.allocated_size,
                file_kind=record.file_kind,
                owner=record.owner,
                flags=record.flags,
                category=category,
                reasons=tuple(dict.fromkeys(finding.reasons)),
                risk=finding.risk,
                actionable=finding.actionable,
                snapshot=CandidateSnapshot(
                    st_dev=record.st_dev,
                    st_ino=record.st_ino,
                    size=record.size,
                    mtime=record.mtime,
                    digest=digest,
                    path=finding.path,
                    safe_root=_safe_root(finding.path, policy),
                ),
                evidence=MappingProxyType(evidence_data),
                proposed_action=action,
                reclaimable_bytes=finding.reclaimable_bytes,
                created_at=run.completed_at or run.started_at,
            )
        )

    ordered = sorted(candidates, key=_candidate_sort_key)
    counters = defaultdict(int)
    identified = []
    for candidate in ordered:
        prefix = _ID_PREFIXES.get(candidate.category, "OTH")
        counters[prefix] += 1
        identified.append(
            replace(
                candidate,
                candidate_id="{}-{:03d}".format(prefix, counters[prefix]),
            )
        )

    plan = CleanupPlan(
        schema_version=SCHEMA_VERSION,
        run_id=run.run_id,
        platform=run.platform,
        created_at=run.completed_at or run.started_at,
        policy_version=policy.version,
        candidates=tuple(identified),
        digest="",
        action=action,
        complete=True,
    )
    return replace(plan, digest=_digest(plan))


def build_permanent_plan(
    run: ScanRun,
    evidence: Iterable[Evidence],
    policy: Policy,
) -> CleanupPlan:
    """Build a plan that is only eligible for permanent deletion."""

    return build_plan(run, evidence, policy, action=ActionType.PERMANENT_DELETE)


def select_ids(plan: CleanupPlan, ids: Iterable[str]) -> Selection:
    """Validate and preserve only the exact candidate IDs explicitly supplied."""

    selected_ids = tuple(ids)
    if not selected_ids:
        raise PlanError("select at least one explicit candidate ID")
    if any(
        not isinstance(candidate_id, str) or not candidate_id
        for candidate_id in selected_ids
    ):
        raise PlanError("candidate IDs must be non-empty strings")
    if len(set(selected_ids)) != len(selected_ids):
        raise PlanError("candidate IDs must not be repeated")

    by_id = {candidate.candidate_id: candidate for candidate in plan.candidates}
    selected = []
    for candidate_id in selected_ids:
        candidate = by_id.get(candidate_id)
        if candidate is None:
            raise PlanError("unknown candidate ID: {}".format(candidate_id))
        if candidate.risk is Risk.REPORT_ONLY:
            raise PlanError(
                "report-only candidate cannot be selected: {}".format(candidate_id)
            )
        if not candidate.actionable:
            raise PlanError(
                "non-actionable candidate cannot be selected: {}".format(candidate_id)
            )
        selected.append(candidate)

    selected_paths = {_normalized_path(candidate.path, plan.platform) for candidate in selected}
    for candidate in selected:
        retained_path = candidate.evidence.get("retained_path")
        if retained_path and _normalized_path(Path(retained_path), plan.platform) in selected_paths:
            raise PlanError("a duplicate's retained copy cannot be selected in the same cleanup")

    return Selection(
        candidate_ids=selected_ids,
        candidates=tuple(selected),
        reclaimable_bytes=sum(item.reclaimable_bytes for item in selected),
        plan_digest=plan.digest,
        action=plan.action,
    )


__all__ = [
    "PlanError",
    "Selection",
    "build_plan",
    "build_permanent_plan",
    "select_ids",
]
