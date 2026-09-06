# Freeup Space Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a macOS + Windows Codex plugin that scans every readable local volume, presents categorized deletion candidates for explicit review, and moves approved items to Trash/Recycle Bin with audit receipts.

**Architecture:** A skills-only plugin bundles focused workflow instructions and a dependency-free Python package. The package separates read-only discovery, analysis, immutable plan generation, platform-specific recoverable deletion, and receipt verification. No MCP server, background service, or permanent-delete fallback is used.

**Tech Stack:** Python 3.11+ standard library, pytest, JSON, Markdown, native macOS `osascript` Finder Trash integration, Windows Shell Recycle Bin API via `ctypes`, Codex plugin manifest and skills.

**Spec:** `docs/superpowers/specs/2026-09-06-freeup-space-design.md`

All plugin-package files in the tasks below are relative to `plugins/freeup-space/`; repository-level design documents and the repo-local marketplace remain at the repository root.

## Global Constraints

- Support macOS and Windows in the first release; do not implement Linux.
- Scan every readable local fixed or removable volume; exclude network mounts unless separately requested.
- Never follow symlinks, junctions, mount aliases, or Windows reparse points during traversal.
- Discovery and analysis are read-only except for run artifacts under `.freeup-space/runs/`.
- Default old-file threshold is 12 months since modification; age is evidence, not proof of unused status.
- Exact duplicates require matching content hashes; matching names, timestamps, or sizes alone are insufficient.
- Candidates are grouped by low, medium, high, and report-only risk; report-only items cannot enter an executable plan.
- Every report entry starts unchecked and has a unique run-scoped candidate ID.
- A deletion action requires candidate IDs explicitly named in a later user message; generic consent is invalid.
- Revalidate path, file identity, size, timestamps, and hash where applicable immediately before acting.
- Normal cleanup moves items to macOS Trash or Windows Recycle Bin and never empties either one.
- Permanent deletion is a separate skill and plan type with fresh explicit confirmation; it is never a fallback.
- Tests never scan or modify a real user machine.

---

### Task 1: Scaffold plugin packaging and developer tooling

**Files:**
- Create: `plugins/freeup-space/.codex-plugin/plugin.json`
- Create: `.agents/plugins/marketplace.json`
- Create: `plugins/freeup-space/pyproject.toml`
- Create: `plugins/freeup-space/.gitignore`
- Create: `plugins/freeup-space/LICENSE`
- Create: `plugins/freeup-space/CONTRIBUTING.md`
- Test: `plugins/freeup-space/tests/test_packaging.py`

**Interfaces:**
- Produces a valid plugin manifest named `freeup-space`, a repo-local marketplace entry, and a Python package test target that later tasks can import.

- [ ] **Step 1: Write failing packaging tests**

```python
def test_manifest_has_required_identity():
    manifest = json.loads(Path(".codex-plugin/plugin.json").read_text())
    assert manifest["name"] == "freeup-space"
    assert manifest["version"]
    assert "description" in manifest

def test_marketplace_points_at_plugin():
    catalog = json.loads(Path(".agents/plugins/marketplace.json").read_text())
    entry = next(p for p in catalog["plugins"] if p["name"] == "freeup-space")
    assert entry["source"]["path"] == "./plugins/freeup-space"
    assert entry["policy"]["installation"] == "AVAILABLE"
    assert entry["policy"]["authentication"] == "ON_INSTALL"
    assert entry["category"] == "Utilities"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd plugins/freeup-space && pytest tests/test_packaging.py -q`
Expected: FAIL because the manifest and catalog do not exist.

- [ ] **Step 3: Create the manifest, catalog, project metadata, and legal files**

Use the plugin creator scaffold or its equivalent, then set the manifest identity and description. The repo-local marketplace source path must be `./plugins/freeup-space`; because the repository itself is the plugin root, add a documented packaging layout or release-copy step that makes this path valid for marketplace testing.

- [ ] **Step 4: Run packaging tests and validators**

Run: `pytest tests/test_packaging.py -q`
Expected: PASS.

Run: `python3 /Users/rampetaravishankar/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/freeup-space`
Expected: validator passes without TODO placeholders.

- [ ] **Step 5: Commit**

```bash
git add .agents plugins/freeup-space
git commit -m "chore: scaffold freeup-space plugin"
```

### Task 2: Define domain models, policy, and protected-path safety

**Files:**
- Create: `src/freeup_space/__init__.py`
- Create: `src/freeup_space/models.py`
- Create: `src/freeup_space/policy.py`
- Create: `src/freeup_space/safety.py`
- Test: `tests/test_policy.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- `Candidate`, `CandidateSnapshot`, `DuplicateGroup`, `ScanRun`, `CleanupPlan`, and `Receipt` dataclasses serialize to JSON-safe dictionaries.
- `Policy.for_platform(platform: str) -> Policy` returns protected roots and category rules.
- `is_protected(path: Path, policy: Policy, platform: str) -> SafetyDecision` explains allow/report-only/reject outcomes.
- `validate_target(path: Path, policy: Policy, snapshot: CandidateSnapshot) -> SafetyDecision` enforces containment and link checks.

- [ ] **Step 1: Write failing model and safety tests**

```python
def test_windows_system_roots_are_report_only():
    decision = is_protected(Path(r"C:\Windows\System32\config"), Policy.for_platform("windows"), "windows")
    assert decision.actionable is False
    assert decision.reason

def test_symlink_target_is_rejected(tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("x")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    decision = validate_target(link, Policy.for_platform("macos"), snapshot_for(link))
    assert decision.actionable is False
```

- [ ] **Step 2: Run targeted tests to verify failure**

Run: `pytest tests/test_policy.py tests/test_safety.py -q`
Expected: FAIL with missing models and policy functions.

- [ ] **Step 3: Implement typed models and platform policies**

Represent risk as an enum, actionability as an explicit boolean, and snapshots with `st_dev`, `st_ino`, size, mtime, and optional digest. Normalize paths without following links. Keep report-only status independent from the presentation layer.

- [ ] **Step 4: Run targeted tests**

Run: `pytest tests/test_policy.py tests/test_safety.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/freeup_space tests/test_policy.py tests/test_safety.py
git commit -m "feat: add safety policy and cleanup models"
```

### Task 3: Implement local-volume enumeration and read-only scanning

**Files:**
- Create: `src/freeup_space/volumes.py`
- Create: `src/freeup_space/scanner.py`
- Test: `tests/test_volumes.py`
- Test: `tests/test_scanner.py`

**Interfaces:**
- `enumerate_local_volumes(platform: str) -> list[Volume]` returns fixed/removable local volumes and excludes network volumes.
- `scan_paths(roots: Iterable[Path], policy: Policy, progress: Callable, cancel: Event) -> ScanRun` yields metadata-only `FileRecord` values and collected errors without deleting or rewriting source files.

- [ ] **Step 1: Write fixture tests first**

```python
def test_scanner_does_not_follow_symlink(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    (real / "file.bin").write_bytes(b"data")
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    run = scan_paths([tmp_path], Policy.for_platform("macos"), lambda _: None, Event())
    assert sum(1 for f in run.files if f.path.name == "file.bin") == 1

def test_permission_error_is_recorded_and_scan_continues(tmp_path):
    # Use a scanner adapter that raises PermissionError for one child.
    run = scan_with_fault_injection(tmp_path, permission_error_name="blocked")
    assert run.errors and (tmp_path / "ok.txt") in {f.path for f in run.files}
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_volumes.py tests/test_scanner.py -q`
Expected: FAIL with missing volume and scanner implementations.

- [ ] **Step 3: Implement platform adapters and traversal**

On macOS enumerate `/` and mounted local volumes via `diskutil`/`mount` parsing without following aliases. On Windows enumerate fixed/removable drive letters with `GetLogicalDrives`/`GetDriveTypeW`. Use `os.scandir`, `lstat`, explicit link/reparse checks, cooperative cancellation, progress callbacks, and bounded error collection.

- [ ] **Step 4: Run tests and a synthetic whole-tree smoke test**

Run: `pytest tests/test_volumes.py tests/test_scanner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/freeup_space tests/test_volumes.py tests/test_scanner.py
git commit -m "feat: add cross-platform read-only scanner"
```

### Task 4: Add category detection, duplicate analysis, and old-file analysis

**Files:**
- Create: `src/freeup_space/categories.py`
- Create: `src/freeup_space/duplicates.py`
- Create: `src/freeup_space/old_files.py`
- Test: `tests/test_categories.py`
- Test: `tests/test_duplicates.py`
- Test: `tests/test_old_files.py`

**Interfaces:**
- `classify(record: FileRecord, policy: Policy) -> list[Evidence]` returns all matching reasons and the highest risk.
- `find_duplicates(files: Iterable[FileRecord], hasher: Hasher) -> list[DuplicateGroup]` confirms exact content matches and excludes hard-link aliases.
- `find_old_files(files: Iterable[FileRecord], now: datetime, months: int = 12) -> list[Evidence]` uses modification time and size thresholds.

- [ ] **Step 1: Write failing category and analysis tests**

```python
def test_same_size_different_content_is_not_duplicate(tmp_path):
    a = make_file(tmp_path / "a.bin", b"aaaa")
    b = make_file(tmp_path / "b.bin", b"bbbb")
    assert find_duplicates([a, b], sha256_hasher) == []

def test_old_file_uses_twelve_month_default(tmp_path):
    record = make_file_with_mtime(tmp_path / "archive.zip", months_ago=13, size=10_000)
    evidence = find_old_files([record], FIXED_NOW)
    assert evidence[0].rule == "old-file-12-months"
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_categories.py tests/test_duplicates.py tests/test_old_files.py -q`
Expected: FAIL with missing analyzers.

- [ ] **Step 3: Implement category rules and two-stage hashing**

Group duplicates by logical size, compute a partial fingerprint for groups with more than one file, then compute SHA-256 only for surviving groups. Detect hard links by `(st_dev, st_ino)`. Implement explicit safe roots and report-only rules for OS-managed data.

- [ ] **Step 4: Run tests and add overlap assertions**

Run: `pytest tests/test_categories.py tests/test_duplicates.py tests/test_old_files.py -q`
Expected: PASS, including one candidate with multiple reasons but one reclaimable total.

- [ ] **Step 5: Commit**

```bash
git add src/freeup_space tests/test_categories.py tests/test_duplicates.py tests/test_old_files.py
git commit -m "feat: classify reclaimable files and detect duplicates"
```

### Task 5: Build immutable review plans and Markdown reports

**Files:**
- Create: `src/freeup_space/plans.py`
- Create: `src/freeup_space/reports.py`
- Test: `tests/test_plans.py`
- Test: `tests/test_reports.py`

**Interfaces:**
- `build_plan(run: ScanRun, evidence: Iterable[Evidence], policy: Policy, action: ActionType = ActionType.TRASH) -> CleanupPlan` creates stable IDs and a digest.
- `render_markdown(plan: CleanupPlan) -> str` renders totals, report-only findings, and unchecked candidate boxes.
- `select_ids(plan: CleanupPlan, ids: Iterable[str]) -> Selection` rejects unknown, report-only, and non-actionable IDs.

- [ ] **Step 1: Write failing plan/report tests**

```python
def test_report_starts_unchecked_and_has_stable_ids(sample_plan):
    first = render_markdown(sample_plan)
    second = render_markdown(sample_plan)
    assert first == second
    assert "- [ ] DUP-" in first

def test_report_only_candidate_cannot_be_selected(sample_plan):
    with pytest.raises(PlanError):
        select_ids(sample_plan, ["SYS-001"])
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_plans.py tests/test_reports.py -q`
Expected: FAIL with missing plan/report functions.

- [ ] **Step 3: Implement plan serialization and report rendering**

Use deterministic ordering by risk, reclaimable bytes, category, and path. Record all snapshot fields and a SHA-256 plan digest. Render duplicate keep/remove details, multiple evidence reasons, volume totals, and moved-versus-freed explanations.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_plans.py tests/test_reports.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/freeup_space tests/test_plans.py tests/test_reports.py
git commit -m "feat: generate explicit cleanup plans and checklists"
```

### Task 6: Implement revalidation and native recoverable deletion

**Files:**
- Create: `src/freeup_space/revalidate.py`
- Create: `src/freeup_space/trash_macos.py`
- Create: `src/freeup_space/trash_windows.py`
- Create: `src/freeup_space/apply.py`
- Test: `tests/test_revalidate.py`
- Test: `tests/test_apply.py`

**Interfaces:**
- `revalidate(candidate: Candidate, policy: Policy) -> SafetyDecision` rejects changed, missing, unsafe, or unverifiable targets.
- `move_to_trash(path: Path, platform: str) -> MoveResult` uses native recoverable deletion.
- `apply_selection(plan: CleanupPlan, ids: Iterable[str], platform: str, dry_run: bool = False) -> Receipt` revalidates and applies selected IDs only.

- [ ] **Step 1: Write failing safety/apply tests**

```python
def test_modified_file_is_skipped(sample_plan, tmp_path):
    target = materialize_candidate(sample_plan, "OLD-001", tmp_path)
    target.write_bytes(b"changed")
    receipt = apply_selection(sample_plan, ["OLD-001"], "macos", dry_run=True)
    assert receipt.skipped[0].reason == "snapshot-mismatch"

def test_empty_trash_is_never_called(monkeypatch, sample_plan):
    monkeypatch.setattr("freeup_space.trash_macos.empty_trash", fail_if_called)
    apply_selection(sample_plan, ["CACHE-001"], "macos", dry_run=True)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_revalidate.py tests/test_apply.py -q`
Expected: FAIL with missing apply and adapter functions.

- [ ] **Step 3: Implement adapters and apply state machine**

Use Finder `osascript`/native Trash behavior on macOS. Use Windows Shell Recycle Bin behavior through a `ctypes` adapter on Windows. Keep adapters injectable for tests. Never use `unlink`, `rmtree`, or a permanent-delete fallback in the normal action path. Record logical moved bytes and before/after free-space readings separately.

- [ ] **Step 4: Run tests and platform adapter lint**

Run: `pytest tests/test_revalidate.py tests/test_apply.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/freeup_space tests/test_revalidate.py tests/test_apply.py
git commit -m "feat: revalidate and move approved files to trash"
```

### Task 7: Add guarded permanent deletion as a separate action

**Files:**
- Create: `src/freeup_space/permanent_delete.py`
- Modify: `src/freeup_space/plans.py`
- Test: `tests/test_permanent_delete.py`

**Interfaces:**
- `build_permanent_plan(run: ScanRun, evidence: Iterable[Evidence], policy: Policy) -> CleanupPlan` marks `action="permanent-delete"`.
- `permanently_delete(plan: CleanupPlan, ids: Iterable[str], platform: str) -> Receipt` requires plan action type and fresh explicit IDs.

- [ ] **Step 1: Write failing isolation tests**

```python
def test_trash_plan_cannot_be_permanently_deleted(trash_plan):
    with pytest.raises(PlanError):
        permanently_delete(trash_plan, ["OLD-001"], "macos")

def test_permanent_delete_requires_exact_ids(permanent_plan):
    with pytest.raises(ApprovalError):
        permanently_delete(permanent_plan, [], "windows")
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_permanent_delete.py -q`
Expected: FAIL with missing permanent-delete implementation.

- [ ] **Step 3: Implement explicit plan-type checks and guarded removal**

Require a non-empty exact ID set, a plan digest that still matches, current snapshots, actionable candidates, and platform confirmation. Exclude report-only and protected paths. The implementation must log every permanent operation and must not be callable from `apply_selection`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_permanent_delete.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/freeup_space tests/test_permanent_delete.py
git commit -m "feat: isolate explicit permanent deletion"
```

### Task 8: Add CLI orchestration and run-artifact persistence

**Files:**
- Create: `src/freeup_space/cli.py`
- Create: `src/freeup_space/runs.py`
- Create: `tests/test_cli.py`
- Modify: `pyproject.toml`
- Modify: `.gitignore`

**Interfaces:**
- `python -m freeup_space scan --all-local-volumes --output .freeup-space/runs/<id>` creates a completed or incomplete run.
- `python -m freeup_space report --run <id>` prints the Markdown checklist.
- `python -m freeup_space apply --run <id> --ids OLD-001,DUP-004` applies recoverable cleanup.
- `python -m freeup_space permanent-delete --run <id> --ids ...` requires a permanent plan.
- `load_run(run_id: str) -> ScanRun` rejects corrupt or incomplete artifacts.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_scan_command_writes_run_artifacts(tmp_path):
    result = runner.invoke(main, ["scan", "--root", str(tmp_path), "--output", str(tmp_path / ".freeup")])
    assert result.exit_code == 0
    assert (tmp_path / ".freeup" / "plan.json").exists()

def test_apply_without_ids_is_rejected(tmp_path):
    result = runner.invoke(main, ["apply", "--run", "run-1"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_cli.py -q`
Expected: FAIL because the CLI and run store do not exist.

- [ ] **Step 3: Implement commands and atomic JSON writes**

Use `argparse`, atomic temp-file replacement inside `.freeup-space/runs/`, explicit `--all-local-volumes` or test roots, structured stderr errors, exit codes, and dry-run support. Require a complete run for report/apply commands and preserve interrupted runs as non-executable.

- [ ] **Step 4: Run CLI tests**

Run: `pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/freeup_space tests/test_cli.py pyproject.toml .gitignore
git commit -m "feat: add freeup-space command line workflow"
```

### Task 9: Write the Codex skills and user-facing documentation

**Files:**
- Create: `skills/freeing-up-space/SKILL.md`
- Create: `skills/finding-duplicates/SKILL.md`
- Create: `skills/finding-old-files/SKILL.md`
- Create: `skills/reviewing-cleanup-candidates/SKILL.md`
- Create: `skills/trashing-approved-files/SKILL.md`
- Create: `skills/permanently-deleting-approved-files/SKILL.md`
- Create: `README.md`
- Create: `SECURITY.md`
- Create: `docs/codex-permissions.md`
- Create: `examples/sample-report.md`
- Test: `tests/test_skill_files.py`

**Interfaces:**
- Each skill has valid `name` and discriminating `description` frontmatter.
- Skills refer to the CLI and enforce the approval protocol without implying permission.
- README documents local/repo marketplace installation, macOS Full Disk Access caveats, Windows protected paths, Ask-for-approval guidance, recovery, and representative prompts.

- [ ] **Step 1: Write failing documentation tests**

```python
def test_all_skills_have_frontmatter_and_safety_language():
    for path in Path("skills").glob("*/SKILL.md"):
        text = path.read_text()
        assert text.startswith("---\n")
        assert "candidate ID" in text or "candidate IDs" in text
        assert "Trash" in text or "Recycle Bin" in text
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_skill_files.py -q`
Expected: FAIL because skill files do not exist.

- [ ] **Step 3: Write concise skills and setup docs**

Keep workflow instructions in the skills, conditional platform details in `docs/codex-permissions.md`, and examples in `examples/`. Explain that Codex sandboxing and approvals are separate controls; recommend the narrowest sufficient permission profile and explicit IDs for mutation.

- [ ] **Step 4: Validate skills and docs**

Run: `pytest tests/test_skill_files.py -q`
Expected: PASS.

Run: `python3 /Users/rampetaravishankar/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/freeing-up-space` for every skill directory.
Expected: every skill validates without placeholders.

- [ ] **Step 5: Commit**

```bash
git add skills README.md SECURITY.md docs/codex-permissions.md examples/sample-report.md tests/test_skill_files.py
git commit -m "docs: add Codex skills and permission guidance"
```

### Task 10: Add CI, end-to-end fixtures, and release verification

**Files:**
- Create: `.github/workflows/test.yml`
- Create: `tests/test_end_to_end.py`
- Modify: `README.md`
- Modify: `SECURITY.md`

**Interfaces:**
- CI runs packaging, unit, integration, and skill validation checks on macOS and Windows.
- End-to-end fixture flow performs scan → report → explicit ID selection → dry-run apply and proves protected files remain untouched.

- [ ] **Step 1: Write failing end-to-end tests**

```python
def test_full_fixture_flow_never_touches_protected_file(tmp_path):
    fixture = make_full_fixture(tmp_path)
    run = audit(fixture.root, platform="macos")
    plan = build_plan(run, run.evidence, Policy.for_platform("macos"))
    receipt = apply_selection(plan, [fixture.cache_id], "macos", dry_run=True)
    assert receipt.succeeded == [fixture.cache_id]
    assert fixture.protected_path.exists()
```

- [ ] **Step 2: Run the complete suite and confirm any failures**

Run: `pytest -q`
Expected: new end-to-end test fails until the fixture helpers and final wiring are complete.

- [ ] **Step 3: Implement fixtures, CI matrix, and release checks**

Use disposable temporary roots only. CI must run `pytest -q`, plugin validation, and every skill validator. Do not grant CI access to a real home directory or host volumes.

- [ ] **Step 4: Run all verification commands locally**

Run: `pytest -q`
Expected: PASS.

Run: `python3 /Users/rampetaravishankar/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .`
Expected: PASS.

Run: `for skill in skills/*; do python3 /Users/rampetaravishankar/.codex/skills/.system/skill-creator/scripts/quick_validate.py "$skill"; done`
Expected: PASS for every skill.

Run: `git diff --check`
Expected: no output.

- [ ] **Step 5: Commit and push the release**

```bash
git add .github tests README.md SECURITY.md
git commit -m "test: add end-to-end verification and CI"
git push origin main
```

## Plan Self-Review

- Spec coverage: discovery categories, protected paths, platform boundaries, duplicate and age heuristics, review IDs, revalidation, Trash/Recycle Bin, permanent deletion, receipts, docs, and tests are mapped to Tasks 2–10.
- Placeholder scan: no TODO/TBD instructions are present; every task names concrete files, interfaces, commands, and expected results.
- Type consistency: `Policy`, `FileRecord`, `Evidence`, `ScanRun`, `CleanupPlan`, `Receipt`, `build_plan`, `select_ids`, `apply_selection`, and platform adapters are introduced before later use.
- Packaging note: Task 1 creates the installable plugin under `plugins/freeup-space/`, matching the repo-local marketplace's required `./plugins/freeup-space` source path.
