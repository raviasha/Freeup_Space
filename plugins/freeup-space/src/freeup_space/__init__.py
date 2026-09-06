"""Freeup Space package."""

from .models import (
    ActionType,
    Candidate,
    CandidateSnapshot,
    CleanupPlan,
    DuplicateGroup,
    Receipt,
    Risk,
    SafetyDecision,
    ScanRun,
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
    "Policy",
    "Receipt",
    "Risk",
    "SafetyDecision",
    "ScanRun",
]
