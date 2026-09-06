---
name: trashing-approved-files
description: Move explicitly approved freeup-space candidates to Trash or the Recycle Bin after exact candidate-ID approval.
---

# Trashing Approved Files

Use this skill when the user has already approved moving specific candidates to Trash or the Recycle Bin.

Before mutating anything:

- Confirm the exact candidate IDs.
- Confirm the target platform.
- Keep the request narrow and limited to the approved IDs.

Use the CLI command that matches the approval:

- `freeup-space apply --run-id <id> --platform <macos|windows> <candidate-id...>`

Set expectations plainly:

- On macOS, the item goes to Trash.
- On Windows, the item goes to the Recycle Bin.
- Recovery depends on the platform's trash mechanism and user settings.

If the platform needs more access, ask for the narrowest sufficient permission and call out any macOS Full Disk Access requirement.
