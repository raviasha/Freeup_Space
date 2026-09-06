"""Deterministic Markdown rendering for cleanup review plans."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import ActionType, Candidate, CleanupPlan, Risk


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


def _details(candidate: Candidate, *, actionable: bool) -> list[str]:
    lines = []
    retained_path = candidate.evidence.get("retained_path")
    if retained_path is not None:
        lines.append("  - Keep: {}".format(_text(retained_path)))
        lines.append(
            "  - {}: {}".format(
                _action(candidate.proposed_action),
                _text(candidate.display_path or candidate.path),
            )
        )
    else:
        lines.append(
            "  - Path: {}".format(_text(candidate.display_path or candidate.path))
        )
        if actionable:
            lines.append(
                "  - Proposed action: {}".format(_action(candidate.proposed_action))
            )
    lines.append("  - Reasons:")
    for reason in candidate.reasons:
        lines.append("    - {}".format(_text(reason)))
    return lines


def render_markdown(plan: CleanupPlan) -> str:
    """Render a stable review report without ever preselecting a candidate."""

    actionable = tuple(
        item
        for item in plan.candidates
        if item.actionable and item.risk is not Risk.REPORT_ONLY
    )
    report_only = tuple(item for item in plan.candidates if item not in actionable)
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
            lines.extend(_details(candidate, actionable=False))
    else:
        lines.append("None.")

    lines.extend(("", "## Actionable checklist", ""))
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
            lines.extend(_details(candidate, actionable=True))
    else:
        lines.append("No actionable candidates.")

    lines.extend(
        (
            "",
            "## Space accounting note",
            "",
            "Moving items to Trash records bytes moved; it does not necessarily free space immediately. Verify observed free space separately and empty Trash manually when you are ready to reclaim it permanently.",
        )
    )
    return "\n".join(lines) + "\n"


__all__ = ["render_markdown"]
