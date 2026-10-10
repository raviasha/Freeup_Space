# Interactive cleanup widget

Freeup Space includes a local stdio MCP server and an MCP Apps HTML resource.
Python 3.9+ is the only server dependency; UI assets are bundled, with no CDN,
external network requests, remote hosting, or API key.

Open the widget in a new Codex task after installing the updated plugin. The flow is:

1. Reopen the latest saved scan, choose an older entry under **Saved scans**, or
   choose **New scan**. For a new scan, choose Quick Scan, Deep Scan, or
   Deep Scan + AI; optionally set specific folders.
2. Watch inventory counters, then a separate count for categorizing files,
   checking duplicates, preparing results and saving. Each stage shows its own
   elapsed time; inventory totals remain as a completed summary. Known totals
   use a progress bar. Unknown totals stay indeterminate, and a delayed update
   is reported as the time since the last real progress event. Cancel is available
   while scanning; an inventory checkpoint cannot be applied as a cleanup plan.
3. Expand categories to see each file or aggregate cache folder, path, size, risk,
   reasons and retained duplicate copy. Report-only findings are disabled. Search
   and paging support large reports. Selections persist across categories/pages;
   nothing is preselected. Bulk selection explicitly selects the visible items.
4. Preview up to 500 selected items. A dry run revalidates every candidate. Any
   changed or blocked item prevents confirmation until the selection is revised.
5. Confirm the exact preview to move items to Trash / Recycle Bin. The engine
   revalidates again at apply time and reports moved, skipped and failed items.

The widget offers no permanent deletion or empty-Trash action. Bytes moved are
shown separately from observed free-space change. Files remain recoverable through
the OS Trash or Recycle Bin when the adapter succeeds.

## AI review

Deep + AI runs the deterministic deep analysis, then asks Codex to investigate the
bounded metadata packet using a host follow-up message. The user does not type a
request. The widget polls for progress and displays recorded assessments, evidence,
uncertainty and uninvestigated counts. If the host rejects a follow-up message, the
widget shows the error and a retry button. It never claims pending AI work is done.
AI notes cannot promote report-only items into selectable cleanup candidates.

## Approval and state

The model-visible `open_widget` tool only opens a session. Start, status, cancel,
preview and confirm controls are app-visible tools. They require a random session
key returned only in tool-result `_meta`, not model-visible text or structured
content. A preview generates a random confirmation token bound to the plan digest
and exact IDs, expires after ten minutes and is invalidated by a new preview.
Repeated confirmation requests are idempotent within the session.

Each scan runs in an isolated subprocess using the existing engine. Widget artifacts
are saved outside cloud-synced folders, under the local application data directory
`Freeup Space/widget-workspaces/<workspace-key>/.freeup-space/runs`. Background
jobs allow progress polling while the host remains responsive. Multiple widgets
can operate without changing the server working directory. Ordinary CLI runs
continue to save artifacts under `.freeup-space/runs` in their working directory.
Deep scans classify each record once and reuse that evidence for duplicate
eligibility and the final plan. The inventory checkpoint is saved before analysis;
no preliminary full plan is built. Widget scans defer the full Markdown report:
generate it explicitly with `report --run-id RUN_ID --full --summary-only` from
the scan’s artifact workspace. Ordinary CLI scans still produce their report automatically.
Large artifacts are streamed to compressed files; plain files from older versions
remain readable. Category totals and the current search page are cached for each
immutable plan, so polling does not recalculate them over the full inventory.
When the host remounts the widget (for example, after switching windows), it uses
the original private session credentials to fetch current server state before
enabling controls. Returning to a visible widget also refreshes live status. A
replayed startup snapshot does not reset a running scan, completed report, preview
or receipt. Reconnection only reads status; it never starts or confirms cleanup.
Connection failures offer a retry without showing stale scan controls.

## Saved results and closing the widget

Opening the widget restores its most recent session for the workspace. A saved
scan can also be requested explicitly with `open_widget(workspace, run_id)`.
Reopening reads existing artifacts; it does not start inventory, duplicate
analysis, AI review, or file moves. Large plans load in the background while the
widget shows **Loading saved results**. Saved cleanup receipts and matching AI
findings are restored too; unfinished AI reviews offer **Retry AI review**.

Checked candidate IDs, the plan digest, category, search and page are saved after
review interactions. Choices are kept separately for each scan. **Clear selection**
explicitly clears those IDs; **New scan** leaves older results available under
**Saved scans**. Existing scans stored by earlier installed versions under the
workspace's hashed Application Support directory are also discoverable.

**Close widget** hides the UI and keeps results and choices. It asks the host to
close the panel when that API is available; otherwise it shows a closed view with
**Reopen saved results**. Hiding does not cancel an in-progress operation. The host
owns panel visibility and may unmount the widget independently; the plugin cannot
force a panel to remain onscreen. Reopening restores the saved review.

Small recovery records live in the user's local application data under
`Freeup Space/widget-state` (Application Support on macOS). Session credentials
are kept in private files (0600 on POSIX), never model-visible tool output. Tests
redirect these records with `FREEUP_WIDGET_STATE_DIR`. New widget scans use local application storage; recovered scan artifacts remain
in their original workspace or legacy storage directory. An interrupted analysis
with a complete inventory offers **Resume saved scan** and reuses that inventory.

Preview confirmation tokens are never saved. After a plugin restart, a previous
preview returns to selection and must be previewed and confirmed again. A changed
plan digest clears saved choices. Interrupted file moves are never automatically
retried. File changes since a scan are checked by the normal dry run and apply
validation. Saved selections are not deletion approval.

Tool metadata alone is not authentication; the private session key is also checked
by the service. File paths never become executable commands or HTML markup.
File moves use the existing race-resistant validation and platform Trash adapters.
OS permissions remain applicable and may require a host approval or Full Disk Access.

## Verification

The MCP launcher sets `cwd` to `.`. Codex resolves that directory relative to the
installed plugin root, so `scripts/freeup_widget.py` is loaded from the installed
package on both macOS and Windows. No source checkout, user-specific path, or
plugin-root variable substitution is required.

Python 3.9+ must be available as `python3` on PATH to the Codex process. On Windows,
check `python3 --version` in PowerShell. If only `py -3` works, enable/install the
`python3` command or configure the MCP interpreter for that Windows installation.
Restart Codex after changing PATH so it inherits the updated environment.

After updating from an older hard-coded-path version, refresh the GitHub marketplace,
reinstall Freeup Space, and start a new Codex task. An enabled plugin or successful
install is not proof that its MCP process started: verify discovery includes
`open_widget`. If it is still missing, inspect the MCP startup error and Python
availability instead of repeatedly reinstalling the same cached version.

Run `python3 -m pytest -q` from the original plugin source location. The marketplace
packaging test expects the personal-marketplace layout; in an isolated copy run
`python3 -m pytest -q --ignore=tests/test_packaging.py` and separately validate the
manifest with Plugin Creator's `validate_plugin.py`.

`tests/widget_harness.py` is a development-only loopback UI test host. It uses
temporary fixture files and simulates Trash moves; it must never be used to apply
a user's cleanup. The production entry point is `scripts/freeup_widget.py`.

Browser regression tests use Playwright and its Chromium browser. Install the test
dependency and browser with `python3 -m pip install playwright` and
`python3 -m playwright install chromium`. To use an existing Chrome installation,
set `FREEUP_TEST_BROWSER_CHANNEL=chrome` when running pytest. The fixture host
replays the original tool result on iframe reload to exercise host remounts.
