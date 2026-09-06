---
name: finding-duplicates
description: Identify duplicate cleanup candidates with the freeup-space CLI and present only the exact candidate IDs that are safe to review.
---

# Finding Duplicates

Use this skill when the task is to discover duplicate-file cleanup candidates with `freeup-space`.

Work in review mode first:

- Run `freeup-space scan` for the target platform.
- Use `freeup-space report` to inspect the duplicate candidates before any action.
- If the user wants a mutation, ask for explicit approval of the exact candidate IDs only.

Keep the approval boundary tight:

- Never broaden the request from specific candidate IDs to "all duplicates" without a fresh approval.
- Do not suggest mutation commands until the review report is in hand.

Recovery guidance:

- Duplicate cleanup normally uses the recoverable Trash or Recycle Bin path when the platform supports it.
- Tell the user that Trash or Recycle Bin recovery is available only for recoverable actions, not for permanent deletion.
