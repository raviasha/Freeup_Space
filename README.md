# Freeup Space for Codex

Review disk cleanup opportunities on **macOS and Windows**, then approve exactly what to remove.

Each new run offers:

- **Quick Scan** — common clutter, a short time budget, minimal AI usage.
- **Deep Scan** — broader deterministic analysis, exact duplicates and older-file candidates.
- **Deep Scan + AI** — Deep Scan plus optional AI investigation of unclear findings.

All modes produce a categorized, unchecked review report. Selecting a mode is never deletion approval.

## Install from GitHub

Add this repository URL as a marketplace source in Codex, then install **Freeup Space**:

```text
https://github.com/raviasha/Freeup_Space
```

If your Codex version provides the marketplace CLI:

```text
codex plugin marketplace add https://github.com/raviasha/Freeup_Space
codex plugin add freeup-space@personal
```

The catalog is named `personal`. If that name conflicts with another installed marketplace,
resolve the source conflict in Codex settings without overwriting the unrelated marketplace.

Python 3.9+ is required; no separate clone or pip install is needed to use the bundled runner.
Start a new Codex task after installing, then ask: **“Help me free up disk space.”**

## Documentation

- [Modes, Mac and Windows commands, review and recovery](plugins/freeup-space/README.md)
- [Computer-access permissions and safety guidance](plugins/freeup-space/docs/codex-permissions.md)
- [Optional AI investigation and content-access boundaries](plugins/freeup-space/skills/freeing-up-space/references/ai-investigation.md)

“Old” means not modified for 12 months, not proven unused. Managed application stores may be
report-only. Ebook sorting, similar-image culling and blur detection are separate workflows,
not included features. Moving files to Trash or Recycle Bin does not necessarily reclaim space
until they are permanently removed.
