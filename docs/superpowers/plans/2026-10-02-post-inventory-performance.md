# Post-inventory performance implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce time from inventory completion to review and show truthful progress during every expensive stage.

**Architecture:** Classify each inventory record once, reuse the evidence for duplicate eligibility and the final plan, and eliminate the full preliminary plan. Keep resumable inventory checkpoints and the existing final plan/approval contract. Defer Markdown export for widget scans and encode individual records in bounded JSON chunks.

**Tech Stack:** Python 3.9+ standard library; existing MCP Apps HTML/JavaScript widget; pytest and Node regression tests.

**Spec:** User-approved design in this chat: classification reuse, deferred reports, and accurate phase progress. Duplicate preselection and cached category totals are included where they fit the existing interfaces; a new database or incremental cleanup approval is outside this fix.

## Global constraints

- Preserve exact candidate IDs, digests, policy decisions, retained duplicate checks, and explicit widget confirmation.
- Do not stop or change the user's existing scan or move user files during testing.
- Preserve CLI report generation and saved-run resume compatibility.
- Work on an isolated checkout of the user's `Freeup_Space` repository, preserving its existing edits.
- Apply only changed source files after verification and a clean patch check. Keep the installed plugin and active scan unchanged.

## Review focus

- Old files, hard links, managed/cloud paths, and duplicate retainers must retain their prior eligibility.
- Interrupted scans must resume from the saved inventory and never allow preliminary cleanup.
- Progress must retain inventory totals while showing work for the current stage.
- Deferred reports must remain available through the explicit report command.
- JSON optimization must preserve canonical output/digests and bounded memory.

## Tasks

- [x] Add failing regression tests for one-pass classification, no preliminary plan, deferred widget exports, staged progress, and canonical encoding.
- [x] Reuse classifications through old-file evidence, duplicate eligibility, and final plan creation; skip impossible duplicate groups before budgeting.
- [x] Remove the preliminary full plan/report; preserve checkpoint notifications and resume, defer widget Markdown export, and speed canonical serialization without changing bytes.
- [x] Add phase progress through classification, duplicate checks, plan construction, saving, and widget loading; render stage counts and cache review totals.
- [x] Run the full existing suite, fixture widget tests, Python 3.9 compatibility checks, and a repeatable synthetic before/after benchmark.
- [x] Apply reviewed changes to the source repository; save a portable patch and report measured results and activation limits.

## Execution notes

- The user identified the source repository as `Freeup_Space`. Implementation uses branch `codex/scan-performance` in an isolated clone based on its local commit `685f474`; existing unrelated working-tree changes are preserved in the original checkout.
- Playwright was installed only in a temporary test environment. Final source checks: **168 passed, 2 platform-specific skips**, including browser fixture tests; Node stage-rendering checks, Python 3.9 compilation, and `git diff --check` passed.
- The patch was applied successfully to the original repository. All six pre-existing modified/untracked files retained their exact contents. A local temporary copy combining this patch with those edits passed **173 tests, with 4 platform/native-test skips**; the original checkout's redundant verification was stopped because iCloud was delaying test-file hydration.
- Independent review found two edge cases, both reproduced and fixed: duplicate progress must count partial/full checks without an invented total, and rewriting a compressed receipt must preserve gzip encoding. Additional tests cover plain/compressed inventory resume and failed artifact commits preserving the previous checkpoint.
- Reproduce the metadata benchmark with `/usr/bin/python3 scripts/benchmark_analysis.py --records 20000 --plugin /path/to/plugin`. It uses synthetic records and temporary artifacts, replaces duplicate content reads, and measures inventory checkpoint saving through final plan preparation. Both versions produce identical plan digests at each size.

| Records | Installed baseline | Patched source | Speedup |
| --- | --- | --- | --- |
| 10,000 | 14.540 s | 4.077 s | 3.57× |
| 20,000 | 27.843 s | 9.044 s | 3.08× |

These measurements exclude drive-dependent duplicate hashing and are not a prediction of whole-drive scan duration. The installed plugin must be updated and its process restarted before new scans use this code; an existing process keeps its loaded code.
