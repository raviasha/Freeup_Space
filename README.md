# Freeup Space

`freeup-space` is a local CLI for finding cleanup candidates and handling them safely through review, approval, and action.

The workflow is intentionally narrow:

1. Scan for candidates.
2. Review the report.
3. Ask for explicit approval of the exact candidate IDs.
4. Apply the approved action with the smallest sufficient permission scope.

Install from a local checkout:

```bash
pip install -e plugins/freeup-space
```

Install from the repo marketplace:

```bash
codex plugins install freeup-space
```

Representative prompts:

- "Scan `/Users/me/Downloads` and show me the duplicate and old-file candidates."
- "Review the report and tell me which candidate IDs are safe to move to Trash."
- "Ask me for approval with only the exact candidate IDs you want to trash."
- "Permanently delete only the explicitly approved candidate IDs."

Permission guidance:

- Ask for approval with the exact candidate IDs you intend to mutate.
- Prefer the narrowest sufficient permission profile.
- If a path needs broader access, ask for that specific access instead of broad, blanket permission.
- On macOS, Full Disk Access may be required for some protected locations.
- On Windows, protected paths may be blocked by policy or may require elevated access.

Recovery guidance:

- Moving items with `apply` uses Trash on macOS and the Recycle Bin on Windows when supported.
- Trash or Recycle Bin recovery is not the same as permanent deletion.
- Permanent deletion has no recovery path through Trash or the Recycle Bin.

Sample report:

- See [examples/sample-report.md](examples/sample-report.md) for a short example of the user-facing review output.
