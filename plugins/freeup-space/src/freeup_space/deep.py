"""Broad deterministic inventory and exact-duplicate analysis, without an LLM."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic

from .categories import classify
from .duplicates import find_duplicates, sha256_hasher
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


def analyze(run, progress_stream=None):
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
    last_progress = [0.0]

    def progress(_):
        if monotonic() - last_progress[0] >= 5:
            print("Analysis: confirming exact duplicates locally", file=progress_stream)
            last_progress[0] = monotonic()

    groups = tuple(find_duplicates(eligible, sha256_hasher, progress=progress))
    coverage = dict(run.coverage, duplicates="complete", duplicate_groups=len(groups),
                    duplicate_eligible_files=len(eligible),
                    analysis="complete", old_file_months=12)
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
