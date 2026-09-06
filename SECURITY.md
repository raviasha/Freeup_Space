# Security

`freeup-space` is designed around explicit approval and narrow mutations.

Use these guardrails when operating it:

- Review candidates before any mutation.
- Ask for approval using exact candidate IDs, not broad categories.
- Prefer recoverable actions to Trash or the Recycle Bin when the plan supports them.
- Use permanent deletion only when the plan and the user explicitly allow it.
- Keep permissions as narrow as possible.

Platform notes:

- macOS may require Full Disk Access for protected locations.
- Windows may block protected paths or require elevated access.

If a path is not accessible, ask for the smallest additional access that would let the task continue.
Keep requests to the narrowest sufficient permission.
