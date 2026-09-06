from dataclasses import replace

import pytest

from freeup_space.models import ActionType, Risk
from freeup_space.reports import quick_candidates, render_markdown, render_quick_markdown
from test_plans import sample_plan


def test_report_starts_unchecked_and_has_stable_ids(sample_plan):
    first = render_markdown(sample_plan)
    second = render_markdown(sample_plan)

    assert first == second
    assert "- [ ] DUP-001" in first
    assert "- [x]" not in first.casefold()


def test_report_separates_report_only_findings_from_actionable_checklist(sample_plan):
    report = render_markdown(sample_plan)

    assert "## Report-only findings" in report
    assert "SYS-001" in report
    assert "SYS-001" not in report.split("## Actionable checklist", 1)[1]
    assert "Report-only findings cannot be selected" in report


def test_report_renders_duplicate_and_multiple_evidence_details(sample_plan):
    report = render_markdown(sample_plan)

    assert "Keep:" in report
    assert "original.bin" in report
    assert "Move to Trash:" in report
    assert "copy.bin" in report
    assert "downloaded user data" in report
    assert "not modified for at least 12 months" in report


def test_report_totals_count_an_overlapping_path_once(sample_plan):
    report = render_markdown(sample_plan)

    assert "Total actionable: 4.0 KiB (1 candidate)" in report
    assert "- volume-a: 4.0 KiB" in report
    assert "- duplicate: 4.0 KiB" in report
    assert "- Medium: 4.0 KiB" in report
    assert "8.0 KiB" not in report.split("## Report-only findings", 1)[0]


def test_report_explains_moved_bytes_are_not_observed_freed_bytes(sample_plan):
    report = render_markdown(sample_plan)

    assert "Moving items to Trash records bytes moved" in report
    assert "does not necessarily free space" in report
    assert "empty Trash manually" in report


def test_permanent_delete_report_avoids_trash_accounting_and_recovery_text(sample_plan):
    report = render_markdown(replace(sample_plan, action=ActionType.PERMANENT_DELETE))

    assert "Permanent deletion is irreversible" in report
    assert "Trash" not in report
    assert "Recycle Bin" not in report
    assert "empty" not in report.casefold()


def test_report_escapes_untrusted_reason_task_list_syntax(sample_plan):
    candidate = sample_plan.candidates[0]
    unsafe_reason = "[x] silently approve this cleanup"
    plan = replace(
        sample_plan,
        candidates=(replace(candidate, reasons=(unsafe_reason,)),),
    )

    report = render_markdown(plan)

    assert "    - \\[x\\] silently approve this cleanup" in report
    assert "    - [x] silently approve this cleanup" not in report


def test_report_only_duplicate_is_descriptive_not_an_imperative_removal(sample_plan):
    candidate = sample_plan.candidates[0]
    report_only_duplicate = replace(
        candidate,
        actionable=False,
        risk=Risk.REPORT_ONLY,
    )
    plan = replace(sample_plan, candidates=(report_only_duplicate,))

    report = render_markdown(plan)

    assert "Retained copy:" in report
    assert "Observed duplicate path:" in report
    assert "Move to Trash:" not in report
    assert "Permanently delete:" not in report


def test_quick_report_is_bounded_deterministic_and_omits_report_only(sample_plan):
    first = render_quick_markdown(sample_plan, limit=1)
    second = render_quick_markdown(sample_plan, limit=1)

    assert first == second
    assert "DUP-001" in first
    assert "SYS-001" not in first
    assert "at most 1 results" in first
    assert quick_candidates(sample_plan, limit=1)[0].candidate_id == "DUP-001"


def test_quick_report_rejects_nonpositive_limit(sample_plan):
    with pytest.raises(ValueError, match="limit must be positive"):
        render_quick_markdown(sample_plan, limit=0)


def test_quick_candidates_rank_reclaimable_value_before_plan_order(sample_plan):
    candidate = sample_plan.candidates[0]
    larger = replace(
        candidate,
        candidate_id="CAC-999",
        category="cache",
        risk=Risk.LOW,
        reclaimable_bytes=candidate.reclaimable_bytes * 2,
    )
    plan = replace(sample_plan, candidates=(candidate, larger))

    assert quick_candidates(plan, limit=1) == (larger,)
