# Interactive cleanup widget

Freeup Space includes a local stdio MCP server and an MCP Apps HTML resource.
Python 3.9+ is the only server dependency; UI assets are bundled, with no CDN,
external network requests, remote hosting, or API key.

Open the widget in a new Codex task after installing the updated plugin. The flow is:

1. Choose Quick Scan, Deep Scan, or Deep Scan + AI; optionally set specific folders.
2. Watch real inventory counters, elapsed time and duplicate-analysis progress.
   There is no estimated percentage when the total work is unknown. Cancel is
   available while scanning; partial results cannot be applied.
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

Each scan runs in an isolated subprocess using the existing engine and task
workspace. Background jobs allow progress polling while the host remains responsive.
Multiple widgets can operate without changing the server working directory.
The CLI saves the scan, plan and receipt under `.freeup-space/runs` in that workspace.
When the host remounts the widget (for example, after switching windows), it uses
the original private session credentials to fetch current server state before
enabling controls. Returning to a visible widget also refreshes live status. A
replayed startup snapshot does not reset a running scan, completed report, preview
or receipt. Reconnection only reads status; it never starts or confirms cleanup.
Connection failures offer a retry without showing stale scan controls.

Sessions and approval tokens are in memory: after a server restart, reopen the
widget and scan again. A completed AI packet is advisory and separately validated.

Tool metadata alone is not authentication; the private session key is also checked
by the service. File paths never become executable commands or HTML markup.
File moves use the existing race-resistant validation and platform Trash adapters.
OS permissions remain applicable and may require a host approval or Full Disk Access.

## Verification

This personal installation uses an absolute path to its local source checkout in
`.mcp.json`. The legacy Codex MCP registration does not expand the
`${CLAUDE_PLUGIN_ROOT}` argument. If the checkout moves or the plugin is installed
on another machine, update that argument to the new `scripts/freeup_widget.py`
path and reinstall. Do not treat an enabled plugin or a successful install as proof
that its MCP process started: verify tool discovery includes `open_widget`.
If tools are missing in an already-new task, diagnose registration and startup
instead of repeatedly asking the user to open another task.

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
