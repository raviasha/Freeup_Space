"""Deterministic Markdown rendering for cleanup review plans."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import ActionType, Candidate, CleanupPlan, Risk


QUICK_CATEGORIES = frozenset(
    {
        "archive",
        "cache",
        "developer-artifact",
        "duplicate",
        "installer",
        "log",
        "temporary",
    }
)


def _bytes(value: int) -> str:
    units = ("bytes", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    unit = units[0]
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "bytes":
        return "{} bytes".format(value)
    return "{:.1f} {}".format(amount, unit)


def _text(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")


def _reason_text(value: object) -> str:
    """Render untrusted reasons as text, never as Markdown task-list syntax."""

    return _text(value).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _action(action: ActionType) -> str:
    return "Move to Trash" if action is ActionType.TRASH else "Permanently delete"


def _total_lines(candidates: Iterable[Candidate]) -> list[str]:
    items = tuple(candidates)
    total = sum(item.reclaimable_bytes for item in items)
    lines = [
        "Total actionable: {} ({} candidate{})".format(
            _bytes(total), len(items), "" if len(items) == 1 else "s"
        ),
        "",
        "### By volume",
        "",
    ]
    for volume, size in _group_totals(items, lambda item: item.volume_id or "unknown"):
        lines.append("- {}: {}".format(_text(volume), _bytes(size)))
    lines.extend(("", "### By category", ""))
    for category, size in _group_totals(items, lambda item: item.category):
        lines.append("- {}: {}".format(_text(category), _bytes(size)))
    lines.extend(("", "### By risk", ""))
    for risk, size in _group_totals(items, lambda item: item.risk.value.title()):
        lines.append("- {}: {}".format(risk, _bytes(size)))
    return lines


def _group_totals(candidates, key):
    totals = defaultdict(int)
    for candidate in candidates:
        totals[key(candidate)] += candidate.reclaimable_bytes
    return sorted(totals.items(), key=lambda item: str(item[0]))


def _details(
    candidate: Candidate, *, actionable: bool, action: ActionType
) -> list[str]:
    lines = []
    retained_path = candidate.evidence.get("retained_path")
    if retained_path is not None:
        if actionable:
            lines.append("  - Keep: {}".format(_text(retained_path)))
            lines.append(
                "  - {}: {}".format(
                    _action(action),
                    _text(candidate.display_path or candidate.path),
                )
            )
        else:
            lines.append("  - Retained copy: {}".format(_text(retained_path)))
            lines.append(
                "  - Observed duplicate path: {}".format(
                    _text(candidate.display_path or candidate.path)
                )
            )
    else:
        lines.append(
            "  - Path: {}".format(_text(candidate.display_path or candidate.path))
        )
        if actionable:
            lines.append(
                "  - Proposed action: {}".format(_action(action))
            )
    lines.append("  - Reasons:")
    for reason in candidate.reasons:
        lines.append("    - {}".format(_reason_text(reason)))
    return lines


def render_markdown(plan: CleanupPlan) -> str:
    """Render a stable review report without ever preselecting a candidate."""

    actionable = tuple(
        item
        for item in plan.candidates
        if item.actionable and item.risk is not Risk.REPORT_ONLY
    )
    actionable_ids = {item.candidate_id for item in actionable}
    report_only = tuple(
        item for item in plan.candidates if item.candidate_id not in actionable_ids
    )
    lines = [
        "# Freeup Space cleanup plan",
        "",
        "Plan digest: `{}`".format(plan.digest),
        "",
        "## Reclaimable totals",
        "",
        *_total_lines(actionable),
        "",
        "## Report-only findings",
        "",
        "Report-only findings cannot be selected for cleanup.",
        "",
    ]
    if report_only:
        for candidate in report_only:
            lines.append(
                "- {} — {} — {} — {}".format(
                    candidate.candidate_id,
                    _bytes(candidate.snapshot.size),
                    candidate.risk.value.title(),
                    candidate.category,
                )
            )
            lines.extend(_details(candidate, actionable=False, action=plan.action))
    else:
        lines.append("None.")

    lines.extend(("", "## Actionable checklist", "",
                  "Check the exact candidate IDs you approve. Reply with the checked IDs;"
                  " the assistant will preview those IDs before moving anything.", ""))
    if actionable:
        for candidate in actionable:
            lines.append(
                "- [ ] {} — {} — {} — {}".format(
                    candidate.candidate_id,
                    _bytes(candidate.reclaimable_bytes),
                    candidate.risk.value.title(),
                    candidate.category,
                )
            )
            lines.extend(_details(candidate, actionable=True, action=plan.action))
    else:
        lines.append("No actionable candidates.")

    lines.extend(("", "## Space accounting note", ""))
    if plan.action is ActionType.TRASH:
        lines.append(
            "Moving items to Trash records bytes moved; it does not necessarily free space immediately. Verify observed free space separately and empty Trash manually when you are ready to reclaim it permanently."
        )
    else:
        lines.append(
            "Permanent deletion is irreversible. Reclaimable totals are estimates; verify observed free space separately after the operation."
        )
    return "\n".join(lines) + "\n"


def quick_candidates(plan: CleanupPlan, limit: int = 20) -> tuple[Candidate, ...]:
    """Return a deterministic, bounded shortlist of lower-risk cleanup candidates."""

    if limit < 1:
        raise ValueError("quick report limit must be positive")
    eligible = [
        item
        for item in plan.candidates
        if item.actionable
        and item.risk in {Risk.LOW, Risk.MEDIUM}
        and item.category in QUICK_CATEGORIES
    ]
    eligible.sort(
        key=lambda item: (
            -item.reclaimable_bytes,
            0 if item.risk is Risk.LOW else 1,
            item.category,
            str(item.path),
        )
    )
    return tuple(eligible[:limit])


def render_quick_markdown(plan: CleanupPlan, limit: int = 20) -> str:
    """Render a stable, token-bounded quick-wins report."""

    selected = quick_candidates(plan, limit)
    eligible = tuple(
        item
        for item in plan.candidates
        if item.actionable
        and item.risk in {Risk.LOW, Risk.MEDIUM}
        and item.category in QUICK_CATEGORIES
    )
    eligible_bytes = sum(item.reclaimable_bytes for item in eligible)
    selected_bytes = sum(item.reclaimable_bytes for item in selected)
    lines = [
        "# Freeup Space quick wins",
        "",
        "Run ID: `{}`".format(plan.run_id),
        "Plan digest: `{}`".format(plan.digest),
        "",
        "Deterministic policy: actionable Low/Medium-risk cache, temporary, log, developer-artifact, confirmed duplicate, installer, and archive candidates; stable plan order; at most {} results.".format(
            limit
        ),
        "",
        "Eligible total: {} ({} candidate{})".format(
            _bytes(eligible_bytes),
            len(eligible),
            "" if len(eligible) == 1 else "s",
        ),
        "Shortlist total: {} ({} candidate{})".format(
            _bytes(selected_bytes),
            len(selected),
            "" if len(selected) == 1 else "s",
        ),
        "",
        "## Actionable shortlist",
        "",
    ]
    if selected:
        for candidate in selected:
            lines.append(
                "- [ ] {} — {} — {} — {}".format(
                    candidate.candidate_id,
                    _bytes(candidate.reclaimable_bytes),
                    candidate.risk.value.title(),
                    candidate.category,
                )
            )
            lines.extend(_details(candidate, actionable=True, action=plan.action))
    else:
        lines.append("No quick-win candidates found.")

    remaining = len(eligible) - len(selected)
    lines.extend(("", "## More options", ""))
    if remaining:
        lines.append(
            "{} additional quick-win candidate{} remain. Increase `--limit` or request the full report.".format(
                remaining, "" if remaining == 1 else "s"
            )
        )
    else:
        lines.append("Request the full report to review every candidate discovered in this run.")
    lines.append(
        "Full report command: `freeup-space report --run-id {} --platform {} --full --summary-only`".format(
            plan.run_id, plan.platform
        )
    )
    lines.extend(("", "## Recovery note", ""))
    lines.append(
        "Approved items are moved to Trash and remain recoverable until Trash is emptied."
        if plan.action is ActionType.TRASH
        else "Permanent deletion is irreversible."
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "QUICK_CATEGORIES",
    "quick_candidates",
    "render_markdown",
    "render_quick_markdown",
]
