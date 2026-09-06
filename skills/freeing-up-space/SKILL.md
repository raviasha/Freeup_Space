---
name: freeing-up-space
description: Free local disk space with the freeup-space CLI by scanning, reviewing, and applying approved cleanup candidates.
---

# Freeing Up Space

Use this skill when the task is to reclaim local disk space with the `freeup-space` CLI.

Follow the tool's approval flow exactly:

- Scan and review first.
- Only mutate files or storage after the user explicitly approves the candidate IDs.
- Ask for approval with the exact candidate ID list, and keep the permission request as narrow as possible.

Use the real commands:

- `freeup-space scan`
- `freeup-space report`
- `freeup-space apply --run-id <id> --platform <macos|windows> <candidate-id...>`
- `freeup-space permanent-delete --run-id <id> --platform <macos|windows> <candidate-id...>`

Keep recovery clear:

- `apply` moves items to Trash on macOS and to the Recycle Bin on Windows when the plan allows recovery.
- `permanent-delete` is only for candidates that the plan marks as permanently deletable.
- If recovery is not possible, say so plainly before any mutation.

Respect platform limits:

- On macOS, some scans or deletions need Full Disk Access.
- On Windows, protected paths may require elevated access or may remain blocked by policy.

Do not imply that approval or sandboxing grants permission. The user must approve the candidate IDs for the selected action.
