"""Freeup Space package."""

from .models import (
    ActionType,
    Candidate,
    CandidateSnapshot,
    CleanupPlan,
    DuplicateGroup,
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

__version__ = "0.1.0"

__all__ = [
    "ActionType",
    "Candidate",
    "CandidateSnapshot",
    "CategoryRule",
    "CleanupPlan",
    "DuplicateGroup",
    "FileRecord",
    "Policy",
    "Receipt",
    "Risk",
    "ScanError",
    "ScanProgress",
    "SafetyDecision",
    "ScanRun",
    "Volume",
]
