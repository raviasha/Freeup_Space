# Task 5 Implementation Report

## Outcome

Implemented immutable cleanup-plan creation, deterministic Markdown review reports,
overlap-safe reclaimable totals, stable run-scoped candidate IDs, SHA-256 plan
digests, duplicate keep/remove context, and strict explicit-ID selection.

Commit: `4c23096 feat: generate explicit cleanup plans and checklists`

## Files

- Created `plugins/freeup-space/src/freeup_space/plans.py`
- Created `plugins/freeup-space/src/freeup_space/reports.py`
- Created `plugins/freeup-space/tests/test_plans.py`
- Created `plugins/freeup-space/tests/test_reports.py`

No files outside the Task 5 implementation/test scope were committed.

## Behavior implemented

### Plan construction

- `build_plan(...)` rejects incomplete scans and platform/policy mismatches.
- Evidence is reconciled through the existing policy-aware analyzer so one path with
  multiple reasons produces one candidate and one reclaimable total.
- Exact duplicate groups are incorporated from `ScanRun.duplicate_groups`; retained
  and removable paths and the confirmed content digest are preserved.
- Every candidate records the complete existing snapshot model (`st_dev`, `st_ino`,
  size, mtime, digest, path, and safe root) and the inventory metadata needed by
  later revalidation (`volume_id`, `file_id`, allocated size, kind, owner, flags).
- Candidate evidence metadata is a read-only mapping and all remaining nested plan
  collections are tuples.
- Candidates are ordered by risk, descending reclaimable bytes, category, and path.
- IDs are assigned deterministically within category prefixes (for example,
  `DUP-001` and `SYS-001`).
- Plan creation time is derived from the completed scan rather than wall-clock time,
  making repeated construction from identical inputs reproducible.
- The plan digest is SHA-256 over canonical, sorted-key JSON for the plan with its
  digest field blanked.
- A report-only policy classification wins over duplicate classification, preventing
  protected duplicate findings from being presented under an actionable-looking
  duplicate identity.

### Selection

- `select_ids(...)` requires at least one exact, non-empty candidate ID.
- It rejects unknown IDs, repeated IDs, report-only candidates, and any other
  candidate marked non-actionable.
- It returns a frozen `Selection` containing exactly the requested IDs/candidates,
  their overlap-safe reclaimable total, the plan digest, and the plan action.

### Markdown rendering

- `render_markdown(...)` is deterministic and never emits a checked checkbox.
- It renders actionable totals by volume, primary category, and risk without adding
  the same candidate again for each evidence reason.
- Report-only observations are separate from the actionable checklist and explicitly
  described as unselectable.
- Duplicate entries show both the retained path and proposed remove path.
- Every candidate lists all reconciled reasons.
- The accounting note distinguishes bytes moved from observed bytes freed and tells
  the user that Trash must be emptied manually to reclaim space permanently.

## TDD evidence

### Initial invocation issue (not accepted as RED)

The brief's literal command was first run from the plugin directory:

```text
$ pytest tests/test_plans.py tests/test_reports.py -q
E   ModuleNotFoundError: No module named 'freeup_space'
2 errors in 0.05s
```

This was an environment/import-path error, so it was not treated as the required
feature failure.

### Valid initial RED

```text
$ PYTHONPATH=src pytest tests/test_plans.py tests/test_reports.py -q
E   ModuleNotFoundError: No module named 'freeup_space.plans'
E   ModuleNotFoundError: No module named 'freeup_space.reports'
2 errors in 0.05s
```

This failed because the required production modules did not exist.

### First GREEN

```text
$ PYTHONPATH=src pytest tests/test_plans.py tests/test_reports.py -q
...........                                                              [100%]
11 passed in 0.04s
```

### Self-review regression RED

A new test exercised a protected item that was also an exact duplicate:

```text
$ PYTHONPATH=src pytest tests/test_plans.py tests/test_reports.py -q
......F......                                                            [100%]
E       AssertionError: assert 'DUP-001' == 'SYS-001'
1 failed, 12 passed in 0.03s
```

The production change made afterward ensures the report-only system classification
overrides duplicate as the primary category.

### Regression GREEN

```text
$ PYTHONPATH=src pytest tests/test_plans.py tests/test_reports.py -q
.............                                                            [100%]
13 passed in 0.02s
```

## Final verification

Fresh post-commit targeted test output:

```text
$ PYTHONPATH=src pytest tests/test_plans.py tests/test_reports.py -q
.............                                                            [100%]
13 passed in 0.02s
```

Fresh post-commit full test output:

```text
$ PYTHONPATH=src pytest -q
........................................................................ [ 82%]
...............                                                          [100%]
87 passed in 0.10s
```

Fresh bytecode compilation:

```text
$ PYTHONPATH=src python3 -m compileall -q src tests
```

Exit code: `0`; no output.

Fresh repository status after commit:

```text
$ git status --short --branch
## codex/freeup-space
```

Commit summary:

```text
4c23096 (HEAD -> codex/freeup-space) feat: generate explicit cleanup plans and checklists
 plugins/freeup-space/src/freeup_space/plans.py   | 301 +++++++++++++++++++++
 plugins/freeup-space/src/freeup_space/reports.py | 149 +++++++++++
 plugins/freeup-space/tests/test_plans.py         | 323 +++++++++++++++++++++++
 plugins/freeup-space/tests/test_reports.py       |  49 ++++
 4 files changed, 822 insertions(+)
```

## Self-review

- Re-read the Task 5 brief and the corresponding design/implementation-plan sections.
- Confirmed exact requested interfaces are present.
- Confirmed future plan usage calls `build_plan(run, run.evidence, policy)` and does
  not require a conflicting `Selection` shape.
- Checked realistic mutations: missing incomplete-scan guard, unstable ordering,
  double-counted overlap, mutable evidence, checked-by-default output, missing
  duplicate retained path, and selection of unknown/report-only/non-actionable IDs
  are each covered by tests.
- `git diff --cached --check` passed before commit.
- No external filesystem content is scanned or mutated by the tests.

## Reviewer remediation (round 1)

- Duplicate groups now fail closed unless the retained path resolves to a distinct
  scanned inventory record, every listed removable path resolves to a different
  scanned record of the same size, the group digest is a SHA-256 value, and the
  reclaimable-byte accounting is internally consistent.
- Permanent-delete reports use permanent-delete labels and irreversible-operation
  accounting only; they do not include Trash/Recycle Bin recovery or emptying
  language.
- Untrusted evidence reasons escape Markdown square brackets, preventing a reason
  such as `[x] approve` from rendering as a checked task-list item.
- Report-only duplicate observations identify the retained and observed paths
  descriptively, without a removal instruction.

### Regression verification

```text
$ PYTHONPATH=src pytest tests/test_plans.py tests/test_reports.py -q
.....................                                                    [100%]
21 passed in 0.04s

$ PYTHONPATH=src pytest -q
........................................................................ [ 75%]
........................                                                 [100%]
96 passed in 0.12s

$ PYTHONPATH=src python3 -m compileall -q src tests
Exit code: 0; no output.

$ git diff --check
Exit code: 0; no output.
```

### Reviewer-fix round 1 evidence

```text
$ PYTHONPATH=src pytest tests/test_plans.py tests/test_reports.py -q
......................                                                   [100%]
22 passed in 0.04s
```

This regression run includes the new duplicate-group guard that rejects a retained path whose inventory record is missing or size-incompatible with the declared duplicate group.
