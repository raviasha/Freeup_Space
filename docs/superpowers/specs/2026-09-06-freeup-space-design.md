# Freeup Space Codex Plugin Design

## Purpose

Freeup Space is a Codex plugin for auditing storage on macOS and Windows, presenting deletion candidates for human review, and moving only explicitly approved items to Trash or Recycle Bin. The Git repository is also a repo-local marketplace; the installable plugin package lives at `plugins/freeup-space/` so its marketplace source path remains `./plugins/freeup-space`.

The first release scans every readable local volume for reclaimable space while preventing direct deletion of operating-system files, installed application bundles, active databases, backups, restore data, or other system-managed content. These protected categories may appear in the report but cannot enter an executable cleanup plan.

## Goals

- Discover common reclaimable storage, including caches, temporary files, duplicates, old files, logs, downloads, installers, backups, developer artifacts, locally downloaded media, abandoned application data, and other well-defined categories.
- Scan all readable local fixed and removable volumes on macOS and Windows.
- Classify every candidate by category, reason, risk, size, and actionability.
- Present a concise Markdown checklist whose entries have stable candidate IDs and start unchecked.
- Require the user to approve specific candidate IDs in a new message before any mutation.
- Move approved items to the platform Trash or Recycle Bin by default.
- Revalidate every approved item immediately before acting on it.
- Produce an auditable receipt and verify the observed free-space change.
- Package the workflow as a repository-installable Codex plugin with clear setup and permission documentation.

## Non-goals

- Automatically deleting files based only on a heuristic.
- Emptying Trash or Recycle Bin.
- Treating a generic response such as "clean everything" or "looks good" as deletion approval.
- Deleting protected or system-managed storage.
- Uninstalling applications, pruning cloud data, deleting snapshots or restore points, or modifying OS features.
- Following network mounts, symlinks, junctions, or reparse points by default.
- Claiming that a file is unused solely because its modification timestamp is old.
- Providing a background daemon, scheduled cleanup, custom MCP server, or graphical interface in the first release.
- Supporting Linux in the first release.

## Plugin Architecture

The repository will contain a skills-and-scripts plugin. It does not require an MCP server or external service.

```text
Freeup_Space/
|-- .agents/plugins/marketplace.json
|-- docs/
`-- plugins/freeup-space/
    |-- .codex-plugin/plugin.json
    |-- skills/
    |   |-- freeing-up-space/
    |   |-- finding-duplicates/
    |   |-- finding-old-files/
    |   |-- reviewing-cleanup-candidates/
    |   |-- trashing-approved-files/
    |   `-- permanently-deleting-approved-files/
    |-- src/freeup_space/
    |-- tests/
    |-- examples/
    |-- README.md
    |-- SECURITY.md
    |-- CONTRIBUTING.md
    |-- LICENSE
    `-- pyproject.toml
```

### Skills

#### `freeing-up-space`

The primary entry point for full storage audits. It selects the relevant platform adapter, runs read-only discovery and analysis, combines and deduplicates findings, presents the review checklist, and hands approved IDs to the trashing workflow only after valid user approval.

#### `finding-duplicates`

Supports targeted duplicate searches and the duplicate-analysis phase of a full audit. It groups regular files by logical size, uses a partial fingerprint to reduce work, and confirms matches with SHA-256. It never treats matching names, timestamps, or sizes as proof of duplication. Hard links to the same file identity do not count as independent reclaimable copies.

#### `finding-old-files`

Supports targeted old-file searches and the age-analysis phase of a full audit. The default threshold is 12 months since modification. Results are described as old-file candidates rather than proven unused files. Access-time metadata may be included when reliable but is not required and is not treated as authoritative.

#### `reviewing-cleanup-candidates`

Transforms a completed inventory into a risk-grouped Markdown checklist and an immutable machine-readable plan. It starts all checklist entries unchecked and shows the candidate ID, size, category, reason, risk, path, and proposed action. Duplicate groups show the proposed retained copy and each removable copy.

#### `trashing-approved-files`

Consumes a completed plan plus the exact candidate IDs named by the user. It rejects blanket approval, unknown IDs, report-only candidates, incomplete scans, and stale or changed candidates. It uses native platform facilities to move validated selections to Trash or Recycle Bin and writes a receipt.

#### `permanently-deleting-approved-files`

Handles the exceptional permanent-deletion workflow. It requires a fresh plan created specifically for permanent deletion plus a new confirmation message naming every candidate ID. It cannot reuse approval from a Trash plan and cannot act on report-only data.

## Implementation Components

The implementation will use Python's standard library and native operating-system commands or APIs. It will not require a package installation or persistent process.

- `cli.py`: command-line entry point with scan, report, apply, and verify operations.
- `scanner.py`: traversal, metadata collection, cancellation, progress, and permission-error recording.
- `volumes.py`: local volume enumeration and network-volume exclusion.
- `categories.py`: category definitions, confidence, risk, and report-only rules.
- `duplicates.py`: size grouping, partial fingerprints, full hashes, hard-link handling, and retained-copy suggestions.
- `old_files.py`: 12-month default age analysis and large-file ranking.
- `plans.py`: immutable plan creation, candidate IDs, state transitions, and serialization.
- `reports.py`: Markdown checklist, summaries, overlap handling, and receipts.
- `safety.py`: protected paths, candidate revalidation, path containment, symlink/reparse-point checks, and plan-integrity checks.
- `trash_macos.py`: native macOS Trash integration.
- `trash_windows.py`: Windows shell Recycle Bin integration.
- `permanent_delete.py`: guarded permanent removal for plans explicitly created for that action.

Scan and plan artifacts will be written beneath `.freeup-space/runs/` in the active workspace. Each run has a unique ID and contains inventory metadata, the review plan, progress state, errors, and any eventual receipt. The directory is excluded from version control.

## Discovery Categories

### Low risk: reproducible or diagnostic data

- Browser caches.
- Application caches with known cache semantics.
- OS user caches that are safe to rebuild.
- Temporary files in known temporary locations.
- Thumbnail, font, shader, and package-manager caches.
- Old logs, diagnostic reports, crash reports, and crash or memory dumps.
- Incomplete downloads and abandoned installer fragments.
- Windows Delivery Optimization and update-download caches that can be reported or delegated to supported OS cleanup facilities.

### Medium risk: user or tool data requiring review

- Exact duplicate regular files, with at least one copy retained.
- Large files not modified for 12 months or longer.
- Old files in Downloads and Desktop.
- Installers, disk images, archives, and likely redundant extracted copies.
- Developer build artifacts such as Xcode DerivedData, Gradle caches, package caches, compiled output, and dependency folders that can be regenerated.
- Old mobile-device backups.
- Mail attachments and downloaded message media.
- Old screenshots, screen recordings, exports, and generated reports.
- Application data plausibly left behind after an application was removed.

### High risk: valuable or context-dependent data

- Personal photo, video, audio, and document collections.
- Locally downloaded streaming or offline media.
- Game content and large media libraries.
- Docker images, containers, volumes, and build caches.
- Virtual machines, emulator images, and simulator data.
- Cloud-synced local content that might be eligible for an online-only operation.

High-risk entries are reviewable recommendations. The plugin must explain the owning application or safer native management path when direct trashing could corrupt state.

### Report-only: never eligible for the deletion plan

- Operating-system files and protected directories.
- Installed application bundles and executable installations.
- Active databases and data stores whose consistency cannot be established.
- Backup volumes and backup sets.
- Time Machine local snapshots.
- Windows restore points, component-store content, and other OS-managed recovery data.
- `Windows.old`, Windows Update cleanup, and similar storage that should be managed with supported Windows facilities.
- Hibernation, swap, page files, virtual-memory files, and reserved storage.
- Other users' private data.
- Cloud-synced files where local removal could propagate as a remote deletion.
- Anything the scanner cannot classify with adequate confidence.

Trash and Recycle Bin contents are reported separately. Moving an item to Trash is the normal cleanup action, so existing Trash contents are not included in the same deletion plan and the plugin never empties them.

## Scan Boundaries

- Enumerate every readable local fixed or removable volume.
- Exclude network shares and remote mounts unless the user explicitly names one in a separate request.
- Do not cross symlinks, junctions, mount aliases, or Windows reparse points during recursive traversal.
- Deduplicate physical files by platform file identity where possible.
- Stay read-only throughout scan and analysis. Creating run artifacts in `.freeup-space/runs/` is the only write before approval.
- Record access-denied paths and continue.
- Support cooperative cancellation and periodic progress output.
- Mark interrupted or failed scans incomplete; incomplete runs cannot produce executable deletion plans.

## Candidate and Plan Model

Every candidate includes:

- Stable run-scoped candidate ID.
- Canonical path and display path.
- Volume identity and file identity when the platform exposes them.
- Logical size and, when available, allocated size.
- File kind, timestamps, ownership, and relevant flags.
- Category, reason, risk, and actionability.
- Evidence such as hash, duplicate group, known cache root, or age threshold.
- Proposed action and expected reclaimable bytes.
- Snapshot fields required for pre-action revalidation.

The plan is generated only from a completed scan. It records its schema version, run ID, platform, creation time, policy version, candidate snapshots, and a digest over deletion-relevant fields. Candidate IDs are unique within the plan and remain stable when the Markdown report is rendered again.

Files that match multiple rules appear once in reclaimable totals. Their report entry lists all reasons and uses the highest applicable risk.

## Review and Approval Protocol

The review report begins with totals by volume, category, and risk, followed by report-only observations and an unchecked actionable checklist.

Example:

```markdown
- [ ] DUP-004 — 2.1 GB — Medium — exact duplicate
  - Keep: /Volumes/Media/Projects/final.mov
  - Move to Trash: /Users/me/Downloads/final copy.mov
- [ ] OLD-019 — 860 MB — Medium — unchanged for 18 months
  - Path: /Users/me/Downloads/archive.zip
```

Approval is valid only when the user sends a new message that explicitly identifies candidate IDs. A blanket response, an ambiguous range, a category name without IDs, or consent recorded before the final plan does not authorize deletion.

The orchestration skill repeats the selected IDs, count, and estimated size before invoking the trashing command. It does not silently add related candidates.

## Revalidation and Trashing

Immediately before acting on an approved candidate, the implementation verifies:

- The plan is complete, current, intact, and belongs to the current platform.
- The candidate ID is actionable and explicitly selected.
- The current path still resolves to the recorded local volume and expected file type.
- The target is not a symlink, junction, reparse point, protected path, filesystem root, home directory, or workspace root.
- Size, timestamps, file identity, and other snapshot fields still match.
- Duplicate candidates still match the confirmed duplicate hash and the retained copy still exists.
- Directory candidates remain within a specifically recognized safe root and have not acquired unexpected protected content.

Changed, missing, unsafe, locked, or unverifiable candidates are skipped. There is no force mode in the first release.

macOS actions use a native Trash operation. Windows actions use the Windows shell Recycle Bin API. The plugin never substitutes permanent removal when trashing fails.

## Permanent Deletion

Permanent deletion is outside the default workflow but is available through a separate first-release skill. It requires a fresh permanent-deletion plan and a new confirmation naming every candidate ID. It cannot reuse approval from a Trash plan, cannot apply to report-only data, and cannot be triggered as a fallback. The same revalidation and receipt guarantees apply.

## Receipts and Verification

Every apply run records:

- Approved candidate IDs.
- Successful Trash or Recycle Bin moves.
- Skipped items and specific reasons.
- Failed items and platform errors.
- Logical bytes moved.
- Start and finish timestamps.
- Free-space readings before and after the run.

Because moving files to Trash or Recycle Bin often does not immediately increase free space, the report distinguishes bytes moved from observed bytes freed. It explains that the user must empty Trash or Recycle Bin manually to reclaim that storage permanently.

## Error Handling

- Permission failures are collected and summarized without stopping unrelated traversal.
- Files that disappear during scanning are omitted or marked transient.
- Hashing failures prevent a file from being declared an exact duplicate.
- Read errors do not produce actionable candidates unless enough independent evidence remains for a safe non-content category.
- Platform API failures never fall back to permanent deletion.
- Corrupt, incompatible, incomplete, or modified plan files are rejected.
- Unexpected internal errors leave the run non-executable and preserve diagnostics without including file contents.

## Testing Strategy

Automated tests operate only on synthetic temporary directory trees.

### Scanner tests

- Known cache, temp, log, download, archive, build-artifact, and protected-path fixtures.
- Permission-denied paths, disappearing files, unusual Unicode names, long names, and sparse files.
- Symlinks on macOS and reparse-point adapter behavior on Windows.
- Volume boundaries, network exclusions, cancellation, and incomplete-run state.

### Duplicate tests

- Same size with different content.
- Same name with different content.
- Exact duplicates with different names and timestamps.
- Hard links and platform file identities.
- Partial-fingerprint collisions resolved by a full SHA-256 comparison.
- Retained-copy existence and at-least-one-copy invariants.

### Plan and safety tests

- Stable unique IDs and deterministic report rendering.
- Overlap deduplication and total reconciliation.
- Unchecked-by-default reports.
- Rejection of generic approval, unknown IDs, report-only IDs, stale plans, changed files, corrupt plans, and incomplete scans.
- Rejection of Trash-plan approvals in the permanent-deletion workflow and vice versa.
- Protected paths, filesystem roots, home directories, and workspace roots cannot become trash targets.

### Platform adapter tests

- Unit tests isolate native Trash and Recycle Bin calls behind adapters.
- Integration tests use disposable fixtures and mocked or opt-in platform boundaries.
- Real Trash/Recycle Bin smoke tests are documented and opt-in; automated tests never touch unrelated user data.

### Acceptance checks

- All automated tests pass on current supported macOS and Windows runners.
- Skill frontmatter and resources pass the skill validator.
- The plugin manifest and marketplace entry pass the plugin validator.
- Example reports contain no real usernames, paths, or file data.
- A clean installation can run representative audit, review, and trash flows from Codex.

## Distribution and Documentation

The repository includes:

- A valid `.codex-plugin/plugin.json` manifest.
- A repo-scoped `.agents/plugins/marketplace.json` entry.
- Installation instructions using the Codex plugin marketplace command.
- Example Codex prompts for full audits, duplicate-only scans, old-file scans, review, and approved cleanup.
- macOS and Windows filesystem-permission guidance.
- A recommendation to keep Codex on **Ask for approval** and grant only the narrowest sufficient filesystem access.
- Explanations of sandbox boundaries, approval policy, Full Disk Access on macOS when needed, Windows protected-path behavior, and why some locations may remain unreadable.
- Recovery instructions for restoring from Trash or Recycle Bin.
- Known limitations and privacy notes explaining that filenames and metadata are processed locally and reports should be reviewed before sharing.

The primary repository target is `https://github.com/raviasha/Freeup_Space`.

## Security Invariants

1. No candidate is deleted during discovery or analysis.
2. No candidate is selected by default.
3. No mutation occurs without explicit, plan-specific candidate IDs from a later user message.
4. No report-only candidate can be converted into an actionable candidate by the reporting or apply layer.
5. No changed or unverifiable candidate is acted upon.
6. No symlink, reparse point, filesystem root, home directory, workspace root, protected OS path, or backup root is trashed.
7. No failure to Trash or Recycle Bin falls back to permanent deletion.
8. The plugin never empties Trash or Recycle Bin.
9. The receipt distinguishes moved bytes from bytes actually freed.
10. Tests never scan or clean the developer's real machine.
