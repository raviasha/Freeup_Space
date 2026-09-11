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
- [ ] Write tests for entry modes, synthetic doctor, UTF-8, and packaged worker command; run RED.
- [ ] Implement minimal dispatch and runtime helpers; run targeted tests GREEN.
- [ ] Review runtime task and exercise actual frozen artifact in Task 3.

## Task 2: Installer core

Files: installer/__init__.py, installer/core.py, installer/tests/test_core.py.
Interface: discover_codex() -> list[Path]; default_install_root() -> Path; install(payload: Path, destination: Path, codex: Path, progress=callable) -> dict; SetupError for actionable failures. Payload contains plugin/ and runtime/freeup-space[.exe] with supporting _internal/. Install result includes version, plugin_root, and marketplace_path.
- [ ] Write filesystem/CLI-boundary tests for install, repair, failure rollback, missing host, collision protection and migration; run RED.
- [ ] Implement versioned staging, supported CLI checks, registration/install/readback and synthetic runtime health check. Preserve symlinks in bundles and UTF-8 command output.
- [ ] Test with fake CLI boundaries and synthetic payload, then review task.

## Task 3: Setup UI, release builds, integration

Files: installer/setup.py, installer/build.py, installer/tests/test_build.py, installer/requirements-build.txt, .github/workflows/release.yml, README.md, plugins/freeup-space/README.md.
- [ ] Add focused build-contract tests, then implement Windows onefile windowed setup and Mac windowed .app packaging around a separately frozen runtime.
- [ ] Add GUI install/repair button, Codex discovery/browse, progress, completion, errors/log, and explicit releases link. Run all mutation work off UI thread.
- [ ] Build runtime and installer on each supported OS/architecture in CI; smoke-test executable --doctor and actual MCP handshake/resource using temporary fixtures and no installed Python dependency.
- [ ] Document release download flow, updates, host prerequisite, architecture and signing status; keep source developer installation documented.
- [ ] Run local existing suite, installer suite, build and smoke checks. Independent whole-branch review; address findings.
- [ ] Merge/push main under existing user authorization. Publish tested release artifacts and verify links; report any platform/signing limitations.

## Execution ledger

- Existing isolated checkout: /tmp/freeup-space-completion-summary-20260911; branch codex/self-contained-setup, base 9eddad5.
- User approved installer direction in this task; prior main merge/push authorization persists.
