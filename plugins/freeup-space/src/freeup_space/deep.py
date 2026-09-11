"""Broad deterministic inventory and exact-duplicate analysis, without an LLM."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic
from collections import defaultdict

from .categories import classify
from .duplicates import DuplicateAnalysisDeadlineExceeded, find_duplicates, sha256_hasher
from .policy import Policy
from .safety import is_protected
from .scanner import scan_paths
from .volumes import enumerate_local_volumes


MODE_NAMES = {"quick": "Quick Scan", "deep": "Deep Scan", "deep-ai": "Deep Scan + AI"}
MODES = [
    {"id": "quick", "name": MODE_NAMES["quick"], "description": "Fast checks of common clutter using fixed rules; minimal AI."},
    {"id": "deep", "name": MODE_NAMES["deep"], "description": "Broader inventory, exact duplicates and older files using fixed rules; minimal AI."},
    {"id": "deep-ai", "name": MODE_NAMES["deep-ai"], "description": "The same Deep Scan plus optional AI investigation of unclear findings; more time and AI."},
]

# Deep analysis is deliberately bounded. The inventory and its fixed-rule findings
# are still complete, but duplicate confirmation must never turn into an
# unbounded content-read of a large volume.
_DUPLICATE_MAX_FILES = 10_000
_DUPLICATE_MAX_BYTES = 1024 * 1024 * 1024
_DUPLICATE_MAX_SECONDS = 120.0


def default_roots(platform):
    # Home is explicit: on modern macOS it may have a different device from /.
    return tuple(dict.fromkeys([Path.home()] + [v.path for v in enumerate_local_volumes(platform)]))


def _inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def inventory(paths, platform, run_id, mode, progress_stream=None):
    progress_stream = progress_stream or sys.stderr
    policy = Policy.for_platform(platform)
    roots = tuple(Path(os.path.abspath(path)) for path in paths)
    home = Path.home()
    excluded = []
    excluded_count = 0
    runtime_roots = (home / ".codex", home / ".cache" / "codex-runtimes",
                     Path(__file__).resolve().parents[2])
    last_progress = [0.0]
    cancel = Event()

    def exclude(path):
        nonlocal excluded_count
        reason = None
        parts = tuple(part.casefold() for part in path.parts)
        if path.name == ".freeup-space":
            reason = "scan artifacts"
        elif any(_inside(path, root) for root in runtime_roots):
            reason = "active plugin/agent runtime"
        elif path.parent == home.parent and path != home:
            reason = "other user or shared home directory"
        elif not is_protected(path, policy, platform).actionable and path not in roots:
            reason = "protected system storage"
        elif "dev" in parts and str(path) == "/dev":
            reason = "device namespace"
        if reason:
            excluded_count += 1
            if len(excluded) < 200:
                excluded.append({"path": str(path), "reason": reason})
            return True
        return False

    def progress(update):
        if monotonic() - last_progress[0] >= 5 or update.complete:
            print("Inventory: {} files, {} directories, {} errors".format(
                update.files_scanned, update.directories_scanned, update.errors), file=progress_stream)
            last_progress[0] = monotonic()

    # Partial coverage is explicit. Only individually inventoried regular files
    # enter analysis; deep mode never offers aggregate directory deletion.
    run = scan_paths(roots, policy, progress, cancel, exclude=exclude, allow_partial=True)
    coverage = dict(run.coverage, roots=[str(root) for root in roots],
                    excluded_count=excluded_count, excluded=excluded,
                    inventory_finished=run.complete, duplicates="pending",
                    scope="readable local storage; protected, linked and other-user paths excluded")
    if excluded_count or coverage.get("skipped_links"):
        coverage["status"] = "partial"
    return replace(run, run_id=run_id, mode=mode, complete=False, coverage=coverage)


def _select_duplicate_records(eligible):
    """Select a deterministic, bounded Pareto-prioritized duplicate scope."""

    folder_records = defaultdict(list)
    for record in eligible:
        folder_records[record.path.parent].append(record)
    folders = sorted(
        folder_records.items(),
        key=lambda item: (-sum(record.size for record in item[1]), str(item[0])),
    )
    total_bytes = sum(record.size for record in eligible)
    target_bytes = int(total_bytes * 0.80)
    target_folders = max(1, int(len(folders) * 0.20)) if folders else 0
    selected = []
    selected_bytes = 0
    skipped_for_byte_budget = 0

    for index, (_, records) in enumerate(folders):
        if index >= target_folders and selected_bytes >= target_bytes:
            break
        for record in sorted(records, key=lambda item: (-item.size, str(item.path))):
            if len(selected) >= _DUPLICATE_MAX_FILES:
                break
            if selected_bytes + record.size > _DUPLICATE_MAX_BYTES:
                skipped_for_byte_budget += 1
                continue
            selected.append(record)
            selected_bytes += record.size
        if len(selected) >= _DUPLICATE_MAX_FILES:
            break

    budget_exhausted = (
        len(selected) >= _DUPLICATE_MAX_FILES
        or selected_bytes >= _DUPLICATE_MAX_BYTES
        or skipped_for_byte_budget > 0
    )
    return tuple(selected), {
        "duplicate_eligible_files": len(eligible),
        "duplicate_eligible_bytes": total_bytes,
        "duplicate_folder_count": len(folders),
        "duplicate_target_folder_count": target_folders,
        "duplicate_target_bytes": target_bytes,
        "duplicate_selected_files": len(selected),
        "duplicate_selected_bytes": selected_bytes,
        "duplicate_selected_folder_count": len({record.path.parent for record in selected}),
        "duplicate_file_budget": _DUPLICATE_MAX_FILES,
        "duplicate_byte_budget": _DUPLICATE_MAX_BYTES,
        "duplicate_time_budget_seconds": _DUPLICATE_MAX_SECONDS,
        "duplicate_selection_limited": budget_exhausted,
        "duplicate_scope": "Pareto-prioritized folders; bounded content analysis",
    }


def analyze(run, progress_stream=None, *, max_seconds=_DUPLICATE_MAX_SECONDS):
    progress_stream = progress_stream or sys.stderr
    policy = Policy.for_platform(run.platform)
    eligible = []
    for record in run.files:
        findings = classify(record, policy)
        # Never hash managed/cloud stores, databases, or protected data merely
        # to find a duplicate. Unclassified regular files may be compared locally.
        if record.file_kind == "regular" and all(
            item.actionable or item.rule == "unclassified" for item in findings
        ):
            eligible.append(record)
    selected, selection_coverage = _select_duplicate_records(eligible)
    last_progress = [0.0]
    progress_count = [0]
    deadline = monotonic() + max_seconds

    def progress(_):
        progress_count[0] += 1
        if monotonic() - last_progress[0] >= 5:
            print(
                "Analysis: checked {} duplicate candidates ({} selected; {:.0f}s budget)".format(
                    progress_count[0], len(selected), max_seconds
                ),
                file=progress_stream,
                flush=True,
            )
            last_progress[0] = monotonic()

    try:
        groups = tuple(
            find_duplicates(
                selected,
                sha256_hasher,
                progress=progress,
                should_continue=lambda: monotonic() < deadline,
            )
        )
    except DuplicateAnalysisDeadlineExceeded:
        coverage = dict(
            run.coverage,
            **selection_coverage,
            duplicates="incomplete",
            duplicate_groups=0,
            duplicate_hashed_files=0,
            duplicate_hashed_bytes=0,
            analysis="duplicate-time-budget-exhausted",
            old_file_months=12,
        )
        return replace(run, complete=True, duplicate_groups=(), coverage=coverage)

    coverage = dict(
        run.coverage,
        **selection_coverage,
        duplicates="complete",
        duplicate_groups=len(groups),
        duplicate_hashed_files=len(selected),
        duplicate_hashed_bytes=selection_coverage["duplicate_selected_bytes"],
        analysis="complete",
        old_file_months=12,
    )
    return replace(run, complete=True, duplicate_groups=groups, coverage=coverage)


def coverage_markdown(run):
    coverage = run.coverage
    return "\n".join([
        "# " + MODE_NAMES[run.mode], "",
        "Coverage: **{}**. {} regular files inventoried; {} errors; {} links skipped; {} paths excluded.".format(
            coverage.get("status", "unknown"), len(run.files), coverage.get("error_count", 0),
            coverage.get("skipped_links", 0), coverage.get("excluded_count", 0)),
        "A completed analysis does not mean every location was readable. See run.json for coverage and errors.",
        "Age means not modified for 12 months, not proven unused. Managed-storage findings require their owning app.",
        "AI investigation is separate and advisory." if run.mode == "deep-ai" else "Classification and ranking use fixed local rules.",
        "", "",
    ])
