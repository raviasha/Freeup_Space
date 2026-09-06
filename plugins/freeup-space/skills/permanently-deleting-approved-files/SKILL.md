---
name: permanently-deleting-approved-files
description: Permanently delete exactly approved freeup-space candidates when the plan and explicit candidate IDs allow it.
---

# Permanently Deleting Approved Files

Use this skill when the user has explicitly approved permanent deletion of specific cleanup candidates.

Before you proceed:

- Verify the exact candidate IDs.
- Confirm that the selected plan marks those candidates as permanently deletable.
- Keep the approval limited to the exact IDs and the exact permanent-delete action.

Use the CLI command:

- `freeup-space permanent-delete --run-id <id> --platform <macos|windows> <candidate-id...>`

Be explicit about recovery:

- There is no Trash or Recycle Bin recovery for permanent deletion.
- Say that plainly before running the command.

If the path is protected, ask for the narrowest sufficient permission rather than broad access.
