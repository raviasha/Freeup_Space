"""Runtime checks exercise only synthetic temporary files and stdio messages."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts/freeup_runtime.py"


def invoke(args, *, cwd, input=None):
    env = dict(os.environ, PATH="", PYTHONIOENCODING="ascii", PYTHONUTF8="0")
    return subprocess.run([sys.executable, str(ENTRY), *args], cwd=cwd, env=env,
                          input=input, capture_output=True, encoding="utf-8", timeout=30)


def test_doctor_checks_resources_and_real_scan_without_path_or_workspace_writes(tmp_path):
    result = invoke(["--doctor"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["version"] == json.loads((ROOT / ".codex-plugin/plugin.json").read_text())["version"]
    assert payload["checks"] == {"widget": True, "cli_scan": True}
    assert list(tmp_path.iterdir()) == []


def test_cli_dispatch_writes_unicode_json_even_with_ascii_console(tmp_path):
    files = tmp_path / "Downloads"
    files.mkdir()
    sample = files / "café-東京.zip"
    sample.write_bytes(b"test payload")
    result = invoke(["--cli", "quick", "--summary-only", "--run-id", "runtime-test", "--", str(files)], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["run_id"] == "runtime-test"
    assert "café-東京.zip" in result.stdout
    assert sample.read_bytes() == b"test payload"


def test_mcp_dispatch_initializes_and_reads_widget(tmp_path):
    from freeup_space.widget_server import URI
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": URI}},
    ]
    result = invoke(["--mcp"], cwd=tmp_path, input="".join(json.dumps(item) + "\n" for item in requests))
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert responses[0]["result"]["serverInfo"]["name"] == "freeup-space"
    assert "<!doctype html>" in responses[1]["result"]["contents"][0]["text"].lower()


@pytest.mark.parametrize("args", [[], ["--unexpected"], ["--doctor", "--extra"], ["--mcp", "--extra"]])
def test_invalid_dispatch_returns_usage_error(args, tmp_path):
    result = invoke(args, cwd=tmp_path)
    assert result.returncode == 2
    assert "usage:" in result.stderr.lower()


def test_frozen_worker_reexecutes_runtime_and_reads_bundle(monkeypatch, tmp_path):
    from freeup_space import runtime
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "freeup-space.exe"))
    assert runtime.plugin_root() == tmp_path / "_internal/plugin"
    assert runtime.cli_command(["modes"]) == [str(tmp_path / "freeup-space.exe"), "--cli", "modes"]


def test_source_worker_remains_compatible():
    from freeup_space import runtime
    assert runtime.plugin_root() == ROOT
    assert runtime.cli_command(["modes"]) == [sys.executable, "-u", str(ROOT / "scripts/freeup_space.py"), "modes"]


def test_doctor_reports_missing_widget_as_failure(monkeypatch, tmp_path, capsys):
    from freeup_space import runtime
    metadata = tmp_path / ".codex-plugin"
    metadata.mkdir()
    (metadata / "plugin.json").write_text('{"version":"test"}', encoding="utf-8")
    monkeypatch.setattr(runtime, "plugin_root", lambda: tmp_path)
    assert runtime.main(["--doctor"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "widget" in payload["error"]


def test_widget_worker_uses_utf8_and_windows_no_console(monkeypatch, tmp_path):
    from freeup_space import runtime, widget_service
    service = widget_service.WidgetService()
    view = service.open(str(tmp_path))
    session = service.session(view["session_id"])
    captured = {}

    class Child:
        stdout = io.StringIO('{"path":"café-東京.zip"}')
        stderr = io.StringIO("")
        returncode = 0

        def wait(self):
            return 0

    def launch(command, **kwargs):
        captured.update(command=command, **kwargs)
        return Child()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(widget_service.subprocess, "Popen", launch)
    result = service.command(session, ["modes"])
    assert result == {"path": "café-東京.zip"}
    assert captured["encoding"] == "utf-8"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["creationflags"] == 0x08000000
    assert captured["stdout"] == subprocess.PIPE
    assert captured["stderr"] == subprocess.PIPE


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell launcher")
def test_unix_launcher_uses_installed_runtime_preserving_arguments(tmp_path):
    plugin = tmp_path / "plugin café 東京"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / "freeup-space"
    shutil.copy2(ROOT / "scripts/freeup-space", launcher)
    runtime = tmp_path / "runtime café 東京"
    runtime.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 37\n', encoding="utf-8")
    runtime.chmod(0o755)
    (plugin / ".runtime-path").write_text(str(runtime) + "\n", encoding="utf-8")
    arguments = ["modes", "spaces and café 東京", "literal$HOME", "semi;colon", "star*", ""]
    result = subprocess.run([str(launcher), *arguments], capture_output=True, encoding="utf-8",
                            env=dict(os.environ, PATH="/usr/bin:/bin"))
    assert result.returncode == 37, result.stderr
    assert result.stdout.splitlines() == ["--cli", *arguments]


@pytest.mark.skipif(os.name != "nt", reason="native Windows launcher")
def test_windows_launcher_reads_utf8_runtime_path_and_preserves_arguments(tmp_path):
    plugin = tmp_path / "plugin café 東京"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / "freeup-space.cmd"
    shutil.copy2(ROOT / "scripts/freeup-space.cmd", launcher)
    runtime = tmp_path / "runtime café 東京 % 100!.cmd"
    runtime.write_text('@echo off\nchcp 65001 >nul\necho %*\nexit /b 37\n', encoding="utf-8")
    (plugin / ".runtime-path").write_text(str(runtime) + "\n", encoding="utf-8")
    result = subprocess.run([str(launcher), "modes", "spaces and café 東京", "literal!bang"],
                            capture_output=True, encoding="utf-8")
    assert result.returncode == 37, result.stderr
    assert result.stdout.strip() == '--cli modes "spaces and café 東京" literal!bang'
