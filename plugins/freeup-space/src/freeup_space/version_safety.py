"""Derive version keepers from immutable saved plan candidates."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable

from .models import Candidate, CleanupPlan
from .recommendations import version_key


def version_keepers(plan: CleanupPlan, selected_ids: Iterable[str]) -> Dict[str, Candidate]:
    """Map selected older revisions to one unselected highest revision in O(N).

    Selecting the highest revision explicitly removes this dependency. Ambiguous
    highest revisions provide no automatic keeper, matching recommendations.
    """
    selected = set(selected_ids)
    families = defaultdict(list)
    for candidate in plan.candidates:
        if candidate.file_kind != "regular":
            continue
        key = version_key(candidate.path, plan.platform)
        if key is not None:
            family, revision = key
            families[family].append((revision, candidate))
    result = {}
    for entries in families.values():
        highest = max(revision for revision, _ in entries)
        latest = [candidate for revision, candidate in entries if revision == highest]
        if len(latest) != 1 or latest[0].candidate_id in selected:
            continue
        keeper = latest[0]
        for revision, candidate in entries:
            if revision < highest and candidate.candidate_id in selected:
                result[candidate.candidate_id] = keeper
    return result
