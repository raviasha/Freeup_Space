# Codex Permissions for Freeup Space

Codex sandboxing and user approval are separate controls.

Every new run offers Quick Scan, Deep Scan, or Deep Scan + AI unless the user already
specified a mode. Modes control investigation depth, not deletion authority.
Quick and Deep classify locally with fixed rules; Deep Scan + AI sends a bounded
metadata packet to Codex for advisory investigation. File-content inspection for AI
requires a separate opt-in naming the relevant files. Local duplicate hashing does
not put file contents into model context.

The CLI workflow still requires:

- a report or review step first
- explicit approval of the exact candidate IDs
- the narrowest sufficient permission scope for the requested action

When a protected location is involved:

- On macOS, Full Disk Access may be necessary for the scan, report, or mutation to succeed.
- On Windows, protected paths may need elevated access or may remain blocked by policy.

Recommended request pattern:

- Ask for the smallest access that will unlock the specific path or action.
- If access is broader than needed, tighten it before retrying.
- If a cleanup plan changes, ask again for the exact new candidate IDs.

Recovery rules:

- `apply` uses Trash on macOS and the Recycle Bin on Windows when available.
- Permanent deletion does not provide Trash or Recycle Bin recovery.

CLI commands:

- `freeup-space start --mode <quick|deep|deep-ai> --summary-only [paths...]`
- `freeup-space modes` (lists choices without scanning)
- `freeup-space scan` (legacy explicit-path inventory)
- `freeup-space report`
- `freeup-space apply --run-id <id> --platform <macos|windows> <candidate-id...>`
- `freeup-space permanent-delete --run-id <id> --platform <macos|windows> <candidate-id...>`
