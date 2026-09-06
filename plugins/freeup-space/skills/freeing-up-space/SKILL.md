---
name: freeing-up-space
description: Find cleanup opportunities on macOS or Windows with Quick Scan, Deep Scan, or Deep Scan + AI, then review and apply explicitly approved candidates.
---

# Freeing Up Space

Use the bundled freeup-space engine for inventory, classification, ranking and cleanup.
Resolve the plugin root as two directories above this skill directory. Do not assume
the runner is installed on PATH or require the user to clone or pip-install the plugin.

## Choose a mode for every new run

If the current request explicitly names a mode, use it. Otherwise present these three
choices and wait for the user's selection before scanning. Do not silently reuse the
previous mode or upgrade a scan to AI investigation:

1. **Quick Scan** (`quick`): fast fixed-rule checks of common clutter; minimal AI.
2. **Deep Scan** (`deep`): broader inventory, exact duplicates, older files and
   application-storage findings; fixed-rule classification and minimal AI.
3. **Deep Scan + AI** (`deep-ai`): the same Deep Scan followed by AI investigation
   of unclear findings; potentially more benefit, more time and AI usage.

Choosing a scan mode is not approval to delete. Preserve explicitly requested roots;
without roots, Quick uses selected home locations and Deep enumerates readable local
storage with protected, other-user, runtime and linked paths excluded.

## Run the selected mode

- macOS: `"<plugin-root>/scripts/freeup-space" start --mode <mode> --summary-only [paths...]`
- Windows PowerShell: `& "<plugin-root>\scripts\freeup-space.cmd" start --mode <mode> --summary-only [paths...]`
- Portable fallback with Python 3.9+: `python "<plugin-root>/scripts/freeup_space.py" start --mode <mode> --summary-only [paths...]`

Quote paths. Run from a writable workspace, keeping the working directory the same for
report and apply commands. If Python is missing, explain that dependency rather than
substituting agent-written scan commands. The runner with no arguments displays the modes.

For Quick and Deep, relay the CLI results: do not independently enumerate files, classify
them using AI, or use shell searches to invent extra cleanup candidates. Poll an existing
long-running process for completion rather than launching duplicate scans. Summaries are
bounded; the complete checkbox report is saved at `report_path`. Show/open that report
for detailed review without loading the whole inventory into the conversation.

Explain returned coverage, including errors, excluded paths and exhausted Quick budgets.
Fixed rules do not guarantee identical live results under time limits or changing filesystems.
A full report only changes presentation; a Deep Scan performs more analysis.
Age means **not modified for 12 months**, not proven unused.

Interrupted deep analysis can reuse a finished inventory with
`start --mode <original-mode> --resume <run-id> --summary-only`. An interrupted inventory
requires a fresh scan. Do not advertise full mid-directory or mid-hash resume.

## Optional AI investigation

Only for `deep-ai`, read [references/ai-investigation.md](references/ai-investigation.md)
and perform the investigation after deterministic analysis. A pending packet is not a
completed AI investigation. AI suggestions remain separate from the cleanup plan.

## Review and approved cleanup

Present the categorized, unchecked report. Explain expected savings, recovery and any
managed-storage recommendations. Ask for the exact candidate IDs and action before cleanup.
No scan mode, AI note, blanket approval, or previous cleanup authorizes new deletions.

Use the same bundled runner for:

- `report --run-id <id> --full --summary-only` to locate the full report.
- `apply --run-id <id> --platform <macos|windows> --dry-run <candidate-id...>` to preview.
- `apply --run-id <id> --platform <macos|windows> <candidate-id...>` for approved moves.

`apply` uses Trash or Recycle Bin when supported. Moving files there does not necessarily
free space immediately. Never substitute permanent deletion for a failed recoverable move.
Permanent deletion uses its separate skill and requires its own explicitly approved plan.
