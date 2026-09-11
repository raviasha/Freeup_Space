# Building Freeup Space Setup

Run on the target OS and architecture, using Python 3.12+ with Tkinter:

```sh
python -m pip install -r installer/requirements-build.txt pytest
python -m pytest installer/tests plugins/freeup-space/tests/test_runtime.py -q
python -m installer.build
```

Build output is `build/setup/`. Windows produces a single windowed `.exe`. macOS produces a
ZIP containing `Freeup Space Setup.app`, preserving framework symlinks. The packaged runtime
and setup both undergo read-only smoke tests with only system utilities on PATH. Tests create
synthetic temporary files and never approve real cleanup. GitHub Actions builds Windows x64,
macOS Apple Silicon and macOS Intel independently.

Setup embeds a tar payload to preserve executable permissions and framework links. Extraction
uses Python's data filter plus strict path/link checks. The installed plugin's launcher config
contains the versioned runtime's absolute path; this machine-specific configuration is generated
at installation time, never committed to the source marketplace. Repeated repairs use distinct
plugin cache versions so cached configuration points at the verified runtime.

The installer uses the host's supported `codex plugin marketplace` and `codex plugin add/remove`
commands. Runtime checks inspect the MCP resource and a tiny synthetic scan before and after
registration. Every version lives in the current user's application-data directory; active
runtime versions are retained. A failed setup restores the prior catalog and attempts to restore
its Codex registration; rollback failures are surfaced in setup details.

## Signing and releases

These initial release artifacts are unsigned (macOS binaries have required ad-hoc signatures,
which are not Developer ID signatures). No security settings are bypassed. To distribute trusted
Mac builds, set `MACOS_CODESIGN_IDENTITY` in a build environment with its certificate installed,
then notarize/staple the complete app before archiving. Windows Authenticode signing likewise
requires the publisher's certificate and timestamp service. Signing credentials are not bundled
or stored in this repository.

The release workflow uploads tested installers as build artifacts for branch pushes. A `v*` tag
publishes a GitHub release only after all platform package checks pass. Checksums accompany each
installer. Release notes explicitly describe signing status.

Third-party runtime license notices are included in the payload under `licenses/`.

## Codex detection and Windows access errors

Setup prefers the desktop app's bundled CLI and verifies `codex-cli --version` output and
plugin command support before enabling installation. Inaccessible PATH aliases, launchers
that do not identify themselves as the CLI, unsupported CLIs, and timed-out probes are skipped.
Each probe is bounded to 10 seconds. Paths and failure details appear in the live Setup details
log; Retry detection and Choose Codex are recovery controls in that dialog.

Windows execution aliases retain their original path instead of resolving into a protected
package target. Setup never changes package permissions or elevates itself. If no candidate
can be executed, repair/update the Codex installation or contact the device administrator;
setup cannot override a device policy that denies execution.
