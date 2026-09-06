"""Typed domain objects shared by the Freeup Space workflow."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePath
from typing import Any, Dict, Mapping, Optional, Tuple


class Risk(str, Enum):
    """Review risk assigned to a cleanup candidate."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    REPORT_ONLY = "report-only"


class ActionType(str, Enum):
    """Mutation requested by a cleanup plan."""

    TRASH = "trash"
    PERMANENT_DELETE = "permanent-delete"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return value


class JsonSerializable:
    """Mixin for deterministic, JSON-compatible dataclass dictionaries."""

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)

    def as_dict(self) -> Dict[str, Any]:
        """Alias useful to callers that conventionally use ``as_dict``."""

        return self.to_dict()


@dataclass(frozen=True)
class CandidateSnapshot(JsonSerializable):
    """Filesystem identity captured when a candidate is proposed."""

    st_dev: int
    st_ino: int
    size: int
    mtime: float
    digest: Optional[str] = None
    path: Optional[Path] = None
    safe_root: Optional[Path] = None

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        digest: Optional[str] = None,
        safe_root: Optional[Path] = None,
    ) -> "CandidateSnapshot":
        stat_result = path.lstat()
        return cls(
            st_dev=stat_result.st_dev,
            st_ino=stat_result.st_ino,
            size=stat_result.st_size,
            mtime=stat_result.st_mtime,
            digest=digest,
            path=path,
            safe_root=safe_root,
        )


@dataclass(frozen=True)
class Candidate(JsonSerializable):
    candidate_id: str
    path: Path
    category: str
    reasons: Tuple[str, ...]
    risk: Risk
    actionable: bool
    snapshot: CandidateSnapshot
    display_path: Optional[str] = None
    volume_id: Optional[str] = None
    file_id: Optional[str] = None
    size: Optional[int] = None
    allocated_size: Optional[int] = None
    file_kind: Optional[str] = None
    owner: Optional[str] = None
    flags: Tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    proposed_action: ActionType = ActionType.TRASH
    reclaimable_bytes: int = 0
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class DuplicateGroup(JsonSerializable):
    group_id: str
    digest: str
    size: int
    retained_path: Path
    duplicate_paths: Tuple[Path, ...]
    reclaimable_bytes: int = 0


@dataclass
class ScanRun(JsonSerializable):
    run_id: str
    platform: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    complete: bool = False
    files: Tuple[Any, ...] = ()
    candidates: Tuple[Candidate, ...] = ()
    duplicate_groups: Tuple[DuplicateGroup, ...] = ()
    evidence: Tuple[Any, ...] = ()
    errors: Tuple[Any, ...] = ()


@dataclass(frozen=True)
class CleanupPlan(JsonSerializable):
    schema_version: str
    run_id: str
    platform: str
    created_at: datetime
    policy_version: str
    candidates: Tuple[Candidate, ...]
    digest: str
    action: ActionType = ActionType.TRASH
    complete: bool = True


@dataclass(frozen=True)
class Receipt(JsonSerializable):
    run_id: str
    platform: str
    created_at: datetime
    approved_ids: Tuple[str, ...] = ()
    moved: Tuple[Any, ...] = ()
    skipped: Tuple[Any, ...] = ()
    failed: Tuple[Any, ...] = ()
    logical_moved_bytes: int = 0
    free_space_before: Optional[int] = None
    free_space_after: Optional[int] = None
    plan_digest: Optional[str] = None


@dataclass(frozen=True)
class SafetyDecision(JsonSerializable):
    """A machine-readable safety result independent of report rendering."""

    actionable: bool
    outcome: str
    reason: str
