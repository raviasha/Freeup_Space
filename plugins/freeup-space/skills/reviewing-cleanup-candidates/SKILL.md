---
name: reviewing-cleanup-candidates
description: Review freeup-space cleanup plans, explain risks, and ask for narrow approval of exact candidate IDs.
---

# Reviewing Cleanup Candidates

Use this skill when you need to explain a `freeup-space` plan before any cleanup happens.

Use the interactive widget by default. Expandable categories show their counts and
sizes; each contains the specific items and their clickable checkboxes. The user
selects items, previews the exact candidate IDs and confirms Trash / Recycle Bin
inside the widget. Never ask them to type IDs when the widget is available, and
never automate a real user's approval controls. A checked box is selection;
the final confirmation authorizes the exact previewed move.

The following text-based approval rules apply only to a requested CLI fallback.

Focus the review on:

- candidate ID
- path
- reclaimable size
- risk level
- whether the action is recoverable through Trash or Recycle Bin

If the user wants you to proceed, ask for approval using the exact candidate IDs and the exact action.

Keep the permission request narrow:

- Ask for only the IDs needed for the selected action.
- If the user changes scope, re-review the new candidate set before any mutation.

Mention recovery clearly:

- Recoverable actions go to Trash on macOS or the Recycle Bin on Windows when supported.
- Permanent deletion does not have Trash or Recycle Bin recovery.
