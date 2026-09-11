"""Resource discovery, child execution and self-checks for the bundled runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def plugin_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "plugin"
    return Path(__file__).resolve().parents[2]


def cli_command(args) -> list[str]:
    # A frozen sys.executable is the bootloader, not a Python interpreter.
    if getattr(sys, "frozen", False):
        return [sys.executable, "--cli", *args]
    return [sys.executable, "-u", str(plugin_root() / "scripts/freeup_space.py"), *args]


def child_options() -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    # Keep PyInstaller's library environment for children of this same runtime.
    # These processes share the parent's onedir bundle, not a system interpreter.
    options = dict(env=env, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    return options


def doctor() -> dict:
    """Verify bundled assets and a read-only CLI scan in an isolated directory."""
    result = {"ok": False, "version": None, "checks": {}}
    try:
        root = plugin_root()
        metadata = json.loads((root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        result["version"] = metadata["version"]
        if not (root / "assets/widget.html").read_text(encoding="utf-8").strip():
            raise ValueError("Bundled widget is empty")
        result["checks"]["widget"] = True
        with tempfile.TemporaryDirectory(prefix="freeup-space-doctor-") as temporary:
            workspace = Path(temporary).resolve()
            scan_root = workspace / "sample"
            scan_root.mkdir()
            sample = scan_root / "café-東京.txt"
            contents = b"Freeup Space synthetic health check\n"
            sample.write_bytes(contents)
            run_id = "doctor-check"
            child = subprocess.run(cli_command(["scan", "--run-id", run_id, "--", str(scan_root)]),
                                   cwd=workspace, timeout=30, **child_options())
            if child.returncode:
                raise ValueError("CLI scan failed: " + child.stderr.strip()[-2000:])
            artifact = workspace / ".freeup-space/runs" / run_id / "run.json"
            run = json.loads(artifact.read_text(encoding="utf-8"))
            if (not run.get("complete") or len(run.get("files", [])) != 1
                    or Path(run["files"][0]["path"]) != sample
                    or sample.read_bytes() != contents):
                raise ValueError("CLI scan did not verify the synthetic file")
        result["checks"]["cli_scan"] = True
        result["ok"] = True
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
    return result


def main(argv=None) -> int:
    # Frozen apps cannot rely on Python startup environment switches. MCP and
    # JSON output must be UTF-8 even with a Windows legacy console code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--cli":
        from .cli import main as cli_main
        return cli_main(args[1:])
    if args == ["--mcp"]:
        from .widget_server import main as mcp_main
        mcp_main()
        return 0
    if args == ["--doctor"]:
        result = doctor()
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if result["ok"] else 1
    parser = argparse.ArgumentParser(prog="freeup-space", usage="%(prog)s --mcp | --cli [arguments] | --doctor")
    parser.error("choose --mcp, --cli, or --doctor")
