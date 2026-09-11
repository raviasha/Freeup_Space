# Freeup Space

A Codex plugin for reviewing disk cleanup opportunities on **macOS and Windows**.
Choose a mode, expand categories, check individual items, preview, and confirm
recoverable cleanup inside the interactive widget. The plugin records the exact
candidate IDs behind your selection; you do not need to type them.

The widget includes live scan progress, search, paginated category contents,
selection totals, AI findings and cleanup receipts. It supports Trash / Recycle
Bin only. See [the widget guide](docs/interactive-widget.md).

| Mode | What it does | AI usage |
| --- | --- | --- |
| **Quick Scan** | Checks common user cache/log roots and selected home folders; a roughly 45-second budget and a 10-item shortlist. | Minimal: operate the runner and explain results. |
| **Deep Scan** | Inventories broader local storage, confirms exact duplicates, checks 12-month modification age, and reports application-managed storage. | Minimal: fixed-rule classification and ranking. |
| **Deep Scan + AI** | Runs the same Deep Scan, then Codex investigates a bounded set of unclear findings. | More AI; metadata first, file-content inspection requires a separate opt-in. |

Fixed rules give repeatable decisions for the same inventory, policy and environment.
Live files, permissions and Quick's time budget can change results between runs.
Choosing a mode never authorizes deletion.

## Install through a GitHub marketplace

Use this repository link as the marketplace source in Codex:

```text
https://github.com/raviasha/Freeup_Space
```

For versions providing the marketplace CLI, the commands are the same on Mac and Windows:

```text
codex plugin marketplace add https://github.com/raviasha/Freeup_Space
codex plugin add freeup-space@personal
```

The repository catalog currently declares its marketplace name as `personal`.
If Codex reports a name conflict with another marketplace named `personal`, resolve the
source conflict in marketplace settings; do not overwrite an unrelated marketplace.
Start a new Codex task after installing or updating so the new skills load.

**Python 3.9+ is required.** The plugin includes its runner and source; a separate
repository clone, pip install, or virtual-environment activation is not required.
The AI mode uses the Codex agent, not an additional API key or background model service.

## Use in Codex

Ask:

> Help me free up disk space.

Codex opens the widget with Quick Scan, Deep Scan, and Deep Scan + AI. Select the
mode and folders there, then use category and item checkboxes to review cleanup:

> Open the Freeup Space widget.

Quick and Deep leave classification to the local executable. The AI mode additionally
investigates unresolved findings and presents its suggestions separately.

## Bundled runners

From the installed plugin directory, macOS Terminal:

```bash
./scripts/freeup-space
./scripts/freeup-space start --mode quick --summary-only
./scripts/freeup-space start --mode deep --summary-only "$HOME/Downloads"
./scripts/freeup-space start --mode deep-ai --summary-only "$HOME/Documents"
```

Windows PowerShell:

```powershell
& ".\scripts\freeup-space.cmd"
& ".\scripts\freeup-space.cmd" start --mode quick --summary-only
& ".\scripts\freeup-space.cmd" start --mode deep --summary-only "$HOME\Downloads"
& ".\scripts\freeup-space.cmd" start --mode deep-ai --summary-only "E:\"
```

The Windows runner uses `py -3`, falling back to `python`. Both platforms can also run
`python scripts/freeup_space.py`. Use a writable working directory, and keep it the same
for the run's report and cleanup operations. No mode on `start`, or no arguments at all,
prints the three choices and performs no scan.

Without explicit roots, Deep inventories the current user's home and enumerated local
volumes. It excludes protected system paths, other users' home directories, active agent
runtime files, scan artifacts, symlinks and reparse points. Network shares are excluded.
Only individually inventoried files enter Deep analysis; it never offers whole-directory
deletion based on incomplete traversal.

## Reports and review

Artifacts live in `.freeup-space/runs/<run-id>/`:

- `run.json`: inventory, mode, duplicate groups, errors and coverage.
- `plan.json`: deterministic candidates and plan digest.
- `report.md`: the full categorized, unchecked review report.
- `receipt.json`: results of an approved action.
- AI mode only: `investigation.json` and, after Codex investigates, `ai-notes.json`.

`--summary-only` keeps command output bounded and returns the report path.
`report --run-id <id> --full --summary-only` expands the saved report; it does not scan further.

Partial coverage is explicit: successfully inventoried files can be reviewed while errors
and exclusions are reported. Analysis completion does not imply complete disk coverage.
Interrupted duplicate analysis can reuse a finished inventory:

```text
start --mode deep --resume <run-id> --summary-only
```

Use the original mode when resuming. Interrupted inventories need a fresh run. This is
stage-level resume, not a persistent per-directory or per-hash cache.

## AI investigation and privacy

The CLI creates a metadata-only packet of up to 20 unresolved directories (configurable
with `--ai-limit`). Codex performs the actual investigation; invoking `deep-ai` outside
Codex prepares the packet but does not itself call an AI model.

Names and metadata read by Codex are shared with the model. File contents are not included
in the packet. Reading selected contents for AI analysis needs a separate opt-in.
Local duplicate hashing reads bytes on the computer without putting them in model context.

AI notes identify evidence, uncertainty and an advisory action. They cannot modify the
cleanup plan, make a protected finding actionable, or authorize deletion. Read the packet
with `investigate --run-id <id>`; record conclusions with
`record-investigation --run-id <id> --input <notes.json>`.
The [AI workflow](skills/freeing-up-space/references/ai-investigation.md) defines the note schema.

## Coverage and limits

Deep publishes an inventory-complete checkpoint before duplicate hashing, so early
cache, temporary, log, download, developer-artifact and old-file candidates can be
reviewed while exact duplicates are still being confirmed. Duplicate hashing uses a
Pareto-prioritized folder pass: it targets roughly 80% of eligible bytes from roughly
the top 20% of folders first, subject to 10,000 files, 1 GiB and 120-second content-analysis
budgets. If the time budget expires, Deep produces a reviewable plan without duplicate
candidates and labels duplicate coverage incomplete in `run.json`; it never leaves the run
pending. This is not a guarantee that every duplicate was compared.

Deep detects exact regular-file duplicates using local SHA-256, older files, known
cache/log/download locations, installers, archives, developer-artifact paths, and personal
file types. Ownership hints report cloud stores, backups, containers, virtual disks,
AI models, SDK/simulators, mail/media, creative libraries and version-control stores.
These managed-store hints are recommendations to inspect the owning application,
not native application cleanup adapters or proof that a particular item is unused.

Age means **not modified for 12 months**, not proven unused. Similar-image culling,
blur detection and ebook organization are separate workflows, not features of this plugin.
Duplicate hashes compare the primary file stream; metadata/resource forks and alternate
streams are not a claim of archival equivalence. Keep the appropriate original.

Reclaimable totals are estimates, not guaranteed physical savings: shared blocks,
sparse files and snapshots may affect actual space. Moving items to Trash or Recycle Bin
does not normally free space until those items are permanently removed.

## Approved cleanup and permissions

Use the bundled runner with these arguments, replacing IDs with those the user approved:

```text
apply --run-id <id> --platform <macos|windows> --dry-run <candidate-id...>
apply --run-id <id> --platform <macos|windows> <candidate-id...>
```

The report presents each actionable candidate as an unchecked Markdown checkbox containing
one exact candidate ID. Check or name only the IDs you approve. Inventory-stage checkboxes
are provisional and cannot be applied until final analysis completes.

Approval must name exact candidate IDs. Targets and duplicate retained copies are checked
again; changed plans require a new review. AI suggestions are never accepted as IDs.

On macOS, grant the folders needed; Full Disk Access may be needed for some locations.
On Windows, the scanner uses native directory handles and rejects reparse points. Run as a
normal user; inaccessible locations are reported instead of automatically elevating.
See [permission guidance](docs/codex-permissions.md). Recovery uses Trash or Recycle Bin
when supported. Permanent deletion is a separate irreversible workflow; it is never a fallback.

## Development

From the repository root:

```bash
python -m pip install -e plugins/freeup-space
cd plugins/freeup-space
python -m pytest tests
```

CI runs packaging and synthetic tests on macOS and Windows. Tests do not scan or clean
your real files. Native Recycle Bin/Trash actions still warrant manual disposable-file checks.
