"""Widget workflow tests use only temporary files and a fake Trash adapter."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from freeup_space.widget_service import WidgetService, ROOT
from freeup_space.widget_server import Server, TOOLS, URI, widget_result


def finished(session):
    deadline = time.monotonic() + 20
    while session["status"] in {"scanning", "previewing", "moving"}:
        assert time.monotonic() < deadline, session
        time.sleep(.02)
    return session


@pytest.fixture
def scanned(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = tmp_path / "Downloads"
    files.mkdir()
    for i in range(3):
        (files / ("archive%d.zip" % i)).write_bytes(bytes([i]) * 3000)
    service = WidgetService()
    initial = service.open(str(workspace))
    session = service.session(initial["session_id"], initial["key"])
    service.start(session, "quick", "request-12345", [str(files)])
    finished(session)
    assert session["status"] == "review", session["error"]
    yield service, session, files
    service.close()


def preview(service, session, ids=None):
    ids = ids or [service.view(session)["candidates"][0]["candidate_id"]]
    service.preview(session, ids, session["plan"]["digest"])
    finished(session)
    assert session["status"] == "preview", session["error"]
    return session["preview"]


def test_open_does_not_scan_and_secrets_are_metadata_only(tmp_path):
    service = WidgetService()
    view = service.open(str(tmp_path))
    assert view["status"] == "choose-mode"
    assert not (tmp_path / ".freeup-space").exists()
    result = widget_result(view)
    public = json.dumps([result["structuredContent"], result["content"]])
    assert view["key"] not in public
    assert result["_meta"]["widget"]["key"] == view["key"]
    with pytest.raises(ValueError):
        service.session(view["session_id"], "wrong")


def test_categories_contain_real_items_and_scan_progress(scanned):
    service, session, files = scanned
    view = service.view(session, category="archive")
    assert view["categories"] == [{"category": "archive", "count": 3, "actionable_count": 3, "bytes": 9000}]
    assert len(view["candidates"]) == 3
    assert all(Path(c["path"]).parent == files for c in view["candidates"])
    assert session["progress"]["files_scanned"] == 3
    assert not session["preview"]
    assert len(service.view(session, query="archive1")["candidates"]) == 1


def test_invalid_and_stale_selections_are_rejected(scanned):
    service, session, _ = scanned
    digest = session["plan"]["digest"]
    candidate = service.view(session)["candidates"][0]["candidate_id"]
    for ids, d in [([], digest), ([candidate, candidate], digest), (["../../bad"], digest), ([candidate], "stale")]:
        with pytest.raises(ValueError):
            service.preview(session, ids, d)
    assert session["status"] == "review"


def test_preview_is_dry_and_changed_files_block_confirmation(scanned):
    service, session, files = scanned
    candidate = service.view(session)["candidates"][0]
    Path(candidate["path"]).write_bytes(b"changed contents")
    p = preview(service, session, [candidate["candidate_id"]])
    assert p["blocked"]
    with pytest.raises(ValueError):
        service.confirm(session, p["token"])
    assert len(list(files.iterdir())) == 3


def test_preview_expiry_and_bad_token(scanned):
    service, session, files = scanned
    p = preview(service, session)
    with pytest.raises(ValueError):
        service.confirm(session, "wrong-token")
    p["expires_at"] = time.time() - 1
    with pytest.raises(ValueError):
        service.confirm(session, p["token"])
    assert len(list(files.iterdir())) == 3


def test_confirmation_uses_only_preview_ids_and_is_idempotent(scanned, monkeypatch):
    service, session, files = scanned
    p = preview(service, session)
    commands = []
    def fake_command(s, args, progress=False):
        commands.append(args)
        return {"moved": [], "failed": [], "skipped": [], "logical_moved_bytes": 0}
    monkeypatch.setattr(service, "command", fake_command)
    service.confirm(session, p["token"])
    finished(session)
    service.confirm(session, p["token"])
    assert commands == [["apply", "--run-id", session["run_id"], *p["ids"]]]
    assert len(list(files.iterdir())) == 3


def test_second_preview_invalidates_first_token(scanned):
    service, session, _ = scanned
    first = preview(service, session)["token"]
    preview(service, session, [service.view(session)["candidates"][1]["candidate_id"]])
    with pytest.raises(ValueError):
        service.confirm(session, first)


def test_plan_change_prevents_confirmation(scanned):
    service, session, _ = scanned
    p = preview(service, session)
    path = service.directory(session) / "plan.json"
    data = json.loads(path.read_text())
    data["digest"] = "tampered"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        service.confirm(session, p["token"])


def test_repeat_scan_request_does_not_rescan(scanned):
    service, session, files = scanned
    run_id = session["run_id"]
    service.start(session, "quick", "request-12345", [str(files)])
    assert session["run_id"] == run_id


def test_deep_ai_review_remains_advisory(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    files = tmp_path / "files"
    files.mkdir()
    (files / "unknown.bin").write_bytes(b"test" * 1000)
    server = Server()
    view = server.service.open(str(workspace))
    session = server.service.session(view["session_id"])
    server.service.start(session, "deep-ai", "request-deep-ai", [str(files)])
    finished(session)
    assert session["status"] == "review", session["error"]
    with pytest.raises(ValueError):
        server.call("get_investigation", {"session_id": view["session_id"]})
    server.call("request_ai_review", {"session_id": view["session_id"], "key": view["key"]})
    packet = server.call("get_investigation", {"session_id": view["session_id"]})["structuredContent"]
    before = (server.service.directory(session) / "plan.json").read_bytes()
    notes = {"run_id": packet["run_id"], "plan_digest": packet["plan_digest"], "findings": [
        {"finding_id": f["finding_id"], "assessment": "Unknown file type.", "evidence": "Metadata packet.",
         "uncertainty": "Purpose cannot be determined from metadata.", "suggested_action": "no-conclusion"}
        for f in packet["findings"]]}
    server.call("record_investigation", {"session_id": view["session_id"], "notes": notes})
    assert session["ai_status"] == "complete"
    assert (server.service.directory(session) / "plan.json").read_bytes() == before
    assert all(not f["actionable"] for f in session["ai_notes"]["findings"])


def test_stdio_handshake_resources_and_controls(tmp_path):
    messages = [dict(jsonrpc="2.0", id=1, method="initialize", params={"protocolVersion": "2025-06-18"}),
                dict(jsonrpc="2.0", id=2, method="tools/list"),
                dict(jsonrpc="2.0", id=3, method="resources/read", params={"uri": URI}),
                dict(jsonrpc="2.0", id=4, method="tools/call", params={"name": "open_widget", "arguments": {"workspace": str(tmp_path)}})]
    result = subprocess.run([sys.executable, str(ROOT / "scripts/freeup_widget.py")],
                            input="\n".join(json.dumps(m) for m in messages)+"\n", text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line)["result"] for line in result.stdout.splitlines()]
    assert responses[0]["capabilities"] == {"tools": {}, "resources": {}}
    assert responses[2]["contents"][0]["mimeType"] == "text/html;profile=mcp-app"
    assert responses[3]["_meta"]["widget"]["status"] == "choose-mode"
    for name in ("start_scan", "preview_selection", "confirm_trash", "cancel_scan"):
        t = next(t for t in TOOLS if t["name"] == name)
        assert t["_meta"]["ui"]["visibility"] == ["app"]
        assert "key" in t["inputSchema"]["required"]
    assert not any("permanent" in t["name"] for t in TOOLS)


def test_registered_launcher_works_from_an_unrelated_workspace(tmp_path):
    """Catch an unresolved plugin-root variable or dependency on task cwd."""
    config = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["freeup-space"]
    launcher = Path(config["args"][0])
    if not launcher.exists():
        pytest.skip("Personal MCP launcher is not installed on this machine")
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    result = subprocess.run([config["command"], *config.get("args", [])],
                            cwd=tmp_path, env={**os.environ, **config.get("env", {})},
                            input="\n".join(json.dumps(m) for m in messages) + "\n",
                            text=True, encoding="utf-8", capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert any(t["name"] == "open_widget" for t in responses[1]["result"]["tools"])
    assert not list(tmp_path.iterdir()), "Startup must not create a cleanup run"


def test_stdio_uses_utf8_even_with_legacy_console_encoding(tmp_path):
    workspace = tmp_path / "資料-é"
    workspace.mkdir()
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": URI}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "open_widget", "arguments": {"workspace": str(workspace)}}},
    ]
    result = subprocess.run([sys.executable, str(ROOT / "scripts/freeup_widget.py")],
                            input="\n".join(json.dumps(m, ensure_ascii=False) for m in messages)+"\n",
                            text=True, encoding="utf-8", capture_output=True,
                            env={**os.environ, "PYTHONIOENCODING": "cp1252"}, timeout=20)
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line)["result"] for line in result.stdout.splitlines()]
    assert "◫" in responses[0]["contents"][0]["text"]
    assert responses[1]["_meta"]["widget"]["status"] == "choose-mode"
