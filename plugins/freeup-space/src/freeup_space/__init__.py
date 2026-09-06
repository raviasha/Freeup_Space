"""Freeup Space package."""

from .models import (
    ActionType,
    Candidate,
    CandidateSnapshot,
    CleanupPlan,
    DuplicateGroup,
    Evidence,
    EvidenceCandidate,
    FileRecord,
    Receipt,
    Risk,
    ScanError,
    ScanProgress,
    SafetyDecision,
    ScanRun,
    Volume,
)
from .policy import CategoryRule, Policy
from .permanent_delete import ApprovalError, permanently_delete
from .cli import main
from .plans import build_permanent_plan

__version__ = "0.1.0"

__all__ = [
    "ActionType",
    "ApprovalError",
    "Candidate",
    "CandidateSnapshot",
    "CategoryRule",
    "CleanupPlan",
    "DuplicateGroup",
    "Evidence",
    "EvidenceCandidate",
    "FileRecord",
    "Policy",
    "Receipt",
    "Risk",
    "ScanError",
    "ScanProgress",
    "SafetyDecision",
    "ScanRun",
    "Volume",
    "build_permanent_plan",
    "main",
    "permanently_delete",
]
