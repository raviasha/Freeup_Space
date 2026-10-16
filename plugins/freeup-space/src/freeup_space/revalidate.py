"""Pre-action validation for approved cleanup candidates."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional
from .models import Candidate, CandidateSnapshot, SafetyDecision
from .policy import Policy
from .safety import validate_target


def revalidate(
    candidate: Candidate, policy: Policy, *, version_keeper: Optional[Candidate] = None
) -> SafetyDecision:
    """Re-check the current filesystem state before any mutation."""

    decision = validate_target(candidate.path, policy, candidate.snapshot)
    if not decision.actionable and "snapshot" in decision.reason.lower():
        return SafetyDecision(False, decision.outcome, "snapshot-mismatch")
    if decision.actionable and candidate.evidence.get("retained_path"):
        retained = candidate.evidence.get("retained_snapshot")
        if not retained:
            return SafetyDecision(False, "reject", "retained duplicate snapshot is missing")
        path = Path(candidate.evidence["retained_path"])
        snapshot = CandidateSnapshot(path=path, digest=candidate.snapshot.digest, **retained)
        kept = validate_target(path, policy, snapshot)
        if not kept.actionable:
            return SafetyDecision(False, "reject", "retained duplicate changed or is unavailable")
    if decision.actionable and version_keeper is not None:
        # Version contents can differ. Require the recorded identity and times
        # without reading or comparing version contents.
        snapshot = replace(version_keeper.snapshot, digest=None)
        kept = validate_target(version_keeper.path, policy, snapshot)
        if not kept.actionable:
            return SafetyDecision(False, "reject", "latest version keeper changed or is unavailable")
    return decision
