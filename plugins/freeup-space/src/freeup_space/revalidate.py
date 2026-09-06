"""Pre-action validation for approved cleanup candidates."""

from __future__ import annotations

from .models import Candidate, SafetyDecision
from .policy import Policy
from .safety import validate_target


def revalidate(candidate: Candidate, policy: Policy) -> SafetyDecision:
    """Re-check the current filesystem state before any mutation."""

    decision = validate_target(candidate.path, policy, candidate.snapshot)
    if not decision.actionable and "snapshot" in decision.reason.lower():
        return SafetyDecision(False, decision.outcome, "snapshot-mismatch")
    return decision
