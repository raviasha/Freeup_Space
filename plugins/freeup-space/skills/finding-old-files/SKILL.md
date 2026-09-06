---
name: finding-old-files
description: Identify old-file cleanup candidates with the freeup-space CLI and keep approval scoped to exact candidate IDs.
---

# Finding Old Files

Use this skill when the task is to review old or stale files with `freeup-space`.

Do this in order:

- Follow [the scan-mode workflow](../freeing-up-space/SKILL.md), including a fresh mode choice unless already explicit. Use the bundled freeup-space runner's `start --mode <chosen-mode> --summary-only [paths...]`.
- Explain that Deep Scan includes the full 12-month modification-age analysis; Quick only offers its narrower shortlist. Modification age does not prove that a file is unused.
- Review with `freeup-space report`.
- Ask for explicit approval of the exact candidate IDs before any apply step.

Be precise about the action:

- Use the platform-specific recoverable path when the plan supports moving items to Trash or the Recycle Bin.
- Reserve `permanent-delete` for plans that explicitly mark the old-file candidate as permanently deletable.

Do not imply general permission from sandboxing or from a previous approval.
