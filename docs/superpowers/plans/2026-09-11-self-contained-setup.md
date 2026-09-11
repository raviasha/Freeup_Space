# Self-contained setup Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for bounded implementation tasks and independent review.

**Goal:** Distribute self-contained installers that install/repair and verify the local plugin without requiring Python.

**Architecture:** Freeze the console runtime separately from the windowed installer. Stage versioned per-user payloads and use the Codex CLI for marketplace registration and plugin installation. Keep source-based plugin use intact.

**Tech Stack:** Python standard library, Tkinter, PyInstaller, GitHub Actions on Windows and macOS.

**Spec:** docs/superpowers/specs/2026-09-11-self-contained-setup.md

## Global constraints

- No user Python, pip, Git, PATH edits, or terminal commands are required.
- Existing Codex installation/sign-in remains a prerequisite.
- No real cleanup in setup or tests; health checks use synthetic temporary files.
- Never overwrite active runtime versions, unrelated plugins, or personal marketplaces.
- Errors and OS signing status must be reported honestly.

## Task 1: Frozen runtime

Files: plugins/freeup-space/scripts/freeup_runtime.py, src/freeup_space/runtime.py, widget_service.py, tests/test_runtime.py.
Interface: executable --mcp serves stdio; --cli forwards CLI args; --doctor returns JSON with ok and exercises synthetic subprocess scan plus widget resource. Resource root uses bundled plugin directory when frozen. CLI worker dispatch uses --cli when frozen, source script otherwise.
- [x] Write tests for entry modes, synthetic doctor, UTF-8, and packaged worker command; run RED.
- [x] Implement minimal dispatch and runtime helpers; run targeted tests GREEN.
- [x] Review runtime task and exercise actual frozen artifact in Task 3.

## Task 2: Installer core

Files: installer/__init__.py, installer/core.py, installer/tests/test_core.py.
Interface: discover_codex() -> list[Path]; default_install_root() -> Path; install(payload: Path, destination: Path, codex: Path, progress=callable) -> dict; SetupError for actionable failures. Payload contains plugin/ and runtime/freeup-space[.exe] with supporting _internal/. Install result includes version, plugin_root, and marketplace_path.
- [x] Write filesystem/CLI-boundary tests for install, repair, failure rollback, missing host, collision protection and migration; run RED.
- [x] Implement versioned staging, supported CLI checks, registration/install/readback and synthetic runtime health check. Preserve symlinks in bundles and UTF-8 command output.
- [x] Test with fake CLI boundaries and synthetic payload, then review task.

## Task 3: Setup UI, release builds, integration

Files: installer/setup.py, installer/build.py, installer/tests/test_build.py, installer/requirements-build.txt, .github/workflows/release.yml, README.md, plugins/freeup-space/README.md.
- [x] Add focused build-contract tests, then implement Windows onefile windowed setup and Mac windowed .app packaging around a separately frozen runtime.
- [x] Add GUI install/repair button, Codex discovery/browse, progress, completion, errors/log, and explicit releases link. Run all mutation work off UI thread.
- [x] Build runtime and installer on each supported OS/architecture in CI; smoke-test executable --doctor and actual MCP handshake/resource using temporary fixtures and no installed Python dependency.
- [x] Document release download flow, updates, host prerequisite, architecture and signing status; keep source developer installation documented.
- [x] Run local existing suite, installer suite, build and smoke checks. Independent whole-branch review; address findings.
- [ ] Merge/push main under existing user authorization. Publish tested release artifacts and verify links; report any platform/signing limitations.

## Execution ledger

- Existing isolated checkout: /tmp/freeup-space-completion-summary-20260911; branch codex/self-contained-setup, base 9eddad5.
- User approved installer direction in this task; prior main merge/push authorization persists.

- Runtime: 33 focused regressions passed; full plugin suite 175 passed, 3 platform skips.
- Final setup/runtime tests: 41 passed, 1 native-Windows skip locally.
- Actual frozen Mac console runtime and windowed app both pass doctor and MCP resource checks.
- Real Codex install and Repair passed; cached .mcp.json verified against source, old runtime retained, native CLI wrapper verified without user Python.
- Native build CI passed on Windows x64, macOS Apple Silicon and macOS Intel at 712ef13; final recovery patch requires refreshed package checks.
- Independent review approved rollback independence, versioned cache, locking, directory ownership, recovery UI and persistent migration warnings.
- Ruling: build entry point is outside plugins/scripts because freeup_space.py there shadows the package during PyInstaller analysis; reproduced in first frozen build and corrected.
- Ruling: publisher signing remains pending (no repository signing secrets); initial release notes explicitly disclose unsigned/unnotarized packages. No OS protections are bypassed.
