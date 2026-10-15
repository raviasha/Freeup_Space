"""Recovery tests use temporary scans; no real file moves are performed."""
import json
import time
from pathlib import Path

import pytest

from freeup_space.widget_server import Server, widget_result
from freeup_space.widget_service import WidgetService


def wait(session):
    deadline = time.monotonic() + 20
    while session["status"] in {"scanning", "loading", "previewing", "moving"}:
        assert time.monotonic() < deadline, session.get("error")
        time.sleep(.01)
    return session


@pytest.fixture
def saved(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = tmp_path / "Downloads"
    files.mkdir()
    (files / "one.zip").write_bytes(b"one" * 2000)
    (files / "two.zip").write_bytes(b"two" * 1000)
    service = WidgetService()
    view = service.open(str(workspace))
    session = service.session(view["session_id"], view["key"])
    service.start(session, "quick", "fixture-request", [str(files)])
    wait(session)
    assert session["status"] == "review", session["error"]
    yield service, session, workspace, files
    service.close()


def test_reopen_completed_scan_without_running_cli(saved, monkeypatch):
    service, session, workspace, files = saved
    second = WidgetService()
    def forbidden(*args, **kwargs):
        pytest.fail("Reopening must only read saved artifacts, not run the CLI")
    monkeypatch.setattr(second, "command", forbidden)
    view = second.open(str(workspace))
    restored = wait(second.session(view["session_id"], view["key"]))
    assert restored["run_id"] == session["run_id"]
    assert restored["status"] == "review"
    assert second.view(restored)["actionable_count"] == 2
    assert len(list(files.iterdir())) == 2


def test_replayed_private_credentials_recover_after_restart(saved):
    service, session, _, _ = saved
    server = Server()
    result = server.call("scan_status", {"session_id": session["session_id"], "key": session["key"]})
    assert not result.get("isError")
    restored = wait(server.service.session(session["session_id"], session["key"]))
    assert restored["run_id"] == session["run_id"]
    assert restored["status"] == "review"
    with pytest.raises(ValueError):
        WidgetService().session(session["session_id"], "wrong-key")


def test_selection_filters_survive_restart_but_confirmation_does_not(saved):
    service, session, _, files = saved
    candidate = service.view(session)["candidates"][0]
    ids = [candidate["candidate_id"]]
    service.save_review(session, ids, session["plan"]["digest"], "archive", "one", 0)
    service.preview(session, ids, session["plan"]["digest"])
    wait(session)
    token = session["preview"]["token"]
    new = WidgetService()
    recovered = wait(new.session(session["session_id"], session["key"]))
    view = new.view(recovered)
    assert {k: v for k, v in view["review_state"].items() if k != "selection_digest"} == {"ids": ids, "category": "archive", "query": "one", "offset": 0,
                                      "plan_digest": session["plan"]["digest"], "similar_group": "", "group_offset": 0}
    assert [c["candidate_id"] for c in view["selected_candidates"]] == ids
    assert recovered["status"] == "review" and recovered["preview"] is None
    with pytest.raises(ValueError):
        new.confirm(recovered, token)
    assert len(list(files.iterdir())) == 2
    public = json.dumps(widget_result(view)["structuredContent"])
    assert token not in public and session["key"] not in public


def test_close_only_hides_and_explicit_reopen_keeps_selection(saved):
    service, session, workspace, _ = saved
    ids = [service.view(session)["candidates"][0]["candidate_id"]]
    service.save_review(session, ids, session["plan"]["digest"], "archive", "", 0)
    service.dismiss(session)
    assert service.view(session)["closed"] is True
    assert service.directory(session).is_dir()
    other = WidgetService()
    old = wait(other.session(session["session_id"], session["key"]))
    assert other.view(old)["closed"] is True
    view = other.open(str(workspace))
    restored = wait(other.session(view["session_id"], view["key"]))
    assert other.view(restored)["closed"] is False
    assert other.view(restored)["review_state"]["ids"] == ids


def test_completed_scan_discoverable_without_session_metadata(saved, monkeypatch, tmp_path):
    service, session, workspace, _ = saved
    for record in service.store.root.glob("*.json"):
        record.unlink()  # Delete only this test fixture's recovery records.
    new = WidgetService()
    view = new.open(str(workspace), run_id=session["run_id"])
    restored = wait(new.session(view["session_id"], view["key"]))
    assert restored["status"] == "review"
    assert restored["run_id"] == session["run_id"]
    assert new.view(restored)["saved_scans"][0]["run_id"] == session["run_id"]


def test_stale_or_invalid_selections_cannot_be_persisted(saved):
    service, session, _, _ = saved
    cid = service.view(session)["candidates"][0]["candidate_id"]
    for ids, digest in [([cid], "outdated"), (["no-such-item"], session["plan"]["digest"]),
                        ([cid, cid], session["plan"]["digest"])]:
        with pytest.raises(ValueError):
            service.save_review(session, ids, digest, "archive", "", 0)
    service.save_review(session, [], session["plan"]["digest"], "", "", 0)
    recovered = wait(WidgetService().session(session["session_id"], session["key"]))
    assert recovered["review_state"]["ids"] == []


def test_run_id_cannot_escape_workspace(saved, tmp_path):
    service, session, workspace, _ = saved
    with pytest.raises(ValueError):
        service.open(str(workspace), run_id="../../outside")
    with pytest.raises(ValueError):
        service.open(str(tmp_path), run_id=session["run_id"])


def test_legacy_installed_scan_reopens_and_uses_original_artifact_folder(saved, tmp_path, monkeypatch):
    import hashlib
    import shutil
    service, session, workspace, _ = saved
    state_root = tmp_path / "legacy-local" / "widget-state"
    key = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:24]
    legacy = state_root.parent / "widget-workspaces" / key
    destination = legacy / ".freeup-space/runs" / session["run_id"]
    destination.parent.mkdir(parents=True)
    shutil.move(str(service.directory(session)), destination)
    monkeypatch.setenv("FREEUP_WIDGET_STATE_DIR", str(state_root))
    new = WidgetService()
    view = new.open(str(workspace))
    recovered = wait(new.session(view["session_id"], view["key"]))
    assert recovered["status"] == "review"
    assert new.directory(recovered) == destination
    cid = new.view(recovered)["candidates"][0]["candidate_id"]
    new.preview(recovered, [cid], recovered["plan"]["digest"])
    wait(recovered)
    assert recovered["status"] == "preview"
    assert recovered["preview"]["blocked"] == []


def test_changed_plan_clears_saved_choices(saved):
    service, session, _, _ = saved
    ids = [service.view(session)["candidates"][0]["candidate_id"]]
    service.save_review(session, ids, session["plan"]["digest"], "archive", "one", 0)
    path = service.directory(session) / "plan.json"
    plan = json.loads(path.read_text())
    plan["digest"] = "different-plan"
    path.write_text(json.dumps(plan))
    new = WidgetService()
    recovered = wait(new.session(session["session_id"], session["key"]))
    assert new.view(recovered)["review_state"]["ids"] == []
    assert new.view(recovered)["selected_candidates"] == []


def test_close_while_loading_does_not_discard_results(saved, monkeypatch):
    from threading import Event
    service, session, workspace, _ = saved
    ready, release = Event(), Event()
    other = WidgetService()
    original = other.load
    def delayed(s, name):
        if name == "plan.json":
            ready.set()
            assert release.wait(10)
        return original(s, name)
    monkeypatch.setattr(other, "load", delayed)
    view = other.open(str(workspace))
    restored = other.session(view["session_id"], view["key"])
    try:
        assert ready.wait(5)
        other.dismiss(restored)
        assert other.view(restored)["closed"]
    finally:
        release.set()
    wait(restored)
    assert restored["status"] == "review"
    assert other.view(restored)["closed"]


def test_recovery_credentials_are_private_and_preview_tokens_are_not_saved(saved, monkeypatch):
    import os
    service, session, _, _ = saved
    cid = service.view(session)["candidates"][0]["candidate_id"]
    service.preview(session, [cid], session["plan"]["digest"])
    wait(session)
    record = Path(os.environ["FREEUP_WIDGET_STATE_DIR"]) / (session["session_id"] + ".json")
    assert session["preview"]["token"] not in record.read_text()
    if os.name != "nt":
        assert record.stat().st_mode & 0o777 == 0o600


def test_switching_saved_scans_recovers_each_scans_selection(saved):
    service, session, _, files = saved
    original_run = session["run_id"]
    ids = [service.view(session)["candidates"][0]["candidate_id"]]
    service.save_review(session, ids, session["plan"]["digest"], "archive", "one", 0)
    service.start(session, "quick", "second-fixture-scan", [str(files)])
    wait(session)
    assert session["run_id"] != original_run
    service.open_saved(session, original_run)
    wait(session)
    assert service.view(session)["review_state"]["ids"] == ids
    assert service.view(session)["review_state"]["query"] == "one"


def test_switching_saved_scans_preserves_completed_receipt(saved, monkeypatch):
    service, session, _, files = saved
    original_run = session["run_id"]
    cid = service.view(session)["candidates"][0]["candidate_id"]
    service.preview(session, [cid], session["plan"]["digest"])
    wait(session)
    receipt = {"moved": [], "failed": [], "skipped": [], "logical_moved_bytes": 0}
    with monkeypatch.context() as patch:
        patch.setattr(service, "command", lambda *a, **k: receipt)
        service.confirm(session, session["preview"]["token"])
        wait(session)
    service.start(session, "quick", "next-receipt-fixture", [str(files)])
    wait(session)
    service.open_saved(session, original_run)
    wait(session)
    assert session["status"] == "done"
    assert session["receipt"] == receipt
    assert service.store.review(session["workspace"], original_run)["receipt"] == receipt


@pytest.mark.parametrize("notes_state", ["pending", "complete", "stale"])
def test_ai_artifacts_recover_with_saved_scan(saved, notes_state):
    service, session, _, _ = saved
    directory = service.directory(session)
    status = json.loads((directory / "status.json").read_text())
    status["mode"] = "deep-ai"
    (directory / "status.json").write_text(json.dumps(status))
    packet = {"run_id": session["run_id"], "plan_digest": session["plan"]["digest"],
              "status": "pending", "findings": [{"finding_id": "INV-001", "directory": "/fixture"}]}
    (directory / "investigation.json").write_text(json.dumps(packet))
    if notes_state != "pending":
        notes = {"run_id": session["run_id"], "plan_digest": packet["plan_digest"], "findings": [
            {"finding_id": "INV-001", "assessment": "Unknown.", "evidence": "Metadata only.",
             "uncertainty": "Purpose unclear.", "suggested_action": "no-conclusion"}]}
        if notes_state == "stale":
            notes["plan_digest"] = "different-plan"
        (directory / "ai-notes.json").write_text(json.dumps(notes))
    other = WidgetService()
    restored = wait(other.session(session["session_id"], session["key"]))
    assert restored["status"] == "review", restored["error"]
    assert restored["ai_finding_count"] == 1
    assert restored["ai_status"] == ("complete" if notes_state == "complete" else "pending")
    assert restored["ai_requested"] is False
    if notes_state == "complete":
        assert restored["ai_notes"]["findings"][0]["directory"] == "/fixture"
        assert restored["ai_notes"]["findings"][0]["actionable"] is False
    else:
        assert restored["ai_notes"] is None


def test_corrupt_workspace_pointer_still_allows_saved_scan_recovery(saved):
    from freeup_space.widget_store import workspace_key
    service, session, workspace, _ = saved
    pointer = service.store.root / ("workspace-" + workspace_key(workspace) + ".json")
    pointer.write_text("{")
    other = WidgetService()
    view = other.open(str(workspace), session["run_id"])
    assert wait(other.session(view["session_id"], view["key"]))["status"] == "review"


def test_new_widget_scans_use_local_application_storage(saved):
    service, session, workspace, _ = saved
    assert service.directory(session).is_relative_to(service.store.root.parent / "widget-workspaces")
    assert not (workspace / ".freeup-space").exists()


def test_interrupted_analysis_can_resume_after_restart(saved, monkeypatch):
    service, session, _, _ = saved
    status_path = service.directory(session) / "status.json"
    status = json.loads(status_path.read_text())
    status.update(complete=False, mode="deep", coverage={"inventory_finished": True, "analysis_pending": True})
    status_path.write_text(json.dumps(status))
    session.update(status="error", mode="deep")
    service.store.save(session)
    other = WidgetService()
    restored = wait(other.session(session["session_id"], session["key"]))
    assert other.view(restored)["can_resume"]
    commands = []
    def finish_analysis(s, args, progress=False):
        commands.append(args)
        status.update(complete=True, coverage={"inventory_finished": True})
        status_path.write_text(json.dumps(status))
        return {"coverage": status["coverage"]}
    monkeypatch.setattr(other, "command", finish_analysis)
    other.resume(restored, "resume-fixture-analysis")
    wait(restored)
    assert commands == [["start", "--mode", "deep", "--resume", session["run_id"], "--summary-only"]]
    assert restored["status"] == "review", restored["error"]
    assert not other.view(restored)["can_resume"]
    assert restored["preview"] is None


def test_interrupted_inventory_without_session_record_can_resume(saved):
    service, session, workspace, _ = saved
    status_path = service.directory(session) / "status.json"
    status = json.loads(status_path.read_text())
    status.update(complete=False, mode="deep", coverage={"inventory_finished": True, "analysis_pending": True})
    status_path.write_text(json.dumps(status))
    for record in service.store.root.glob("*.json"):
        record.unlink()
    other = WidgetService()
    view = other.open(str(workspace), session["run_id"])
    restored = wait(other.session(view["session_id"], view["key"]))
    assert restored["mode"] == "deep"
    assert other.view(restored)["can_resume"]
