"""Performance contracts and user-visible progress using disposable files only."""
import io
import json
import os
import pytest
from collections import Counter
from unittest.mock import Mock

from freeup_space import cli, deep, old_files
from freeup_space.categories import classify
from freeup_space.widget_service import WidgetService


def scan_fixture(tmp_path, monkeypatch):
    root = tmp_path / "Downloads"
    root.mkdir()
    for name, content in (("a.zip", b"same" * 5000), ("b.zip", b"same" * 5000),
                          ("old.pdf", b"document" * 2000)):
        (root / name).write_bytes(content)
    os.utime(root / "old.pdf", (1577836800, 1577836800))
    cloud = root / "OneDrive"
    cloud.mkdir()
    (cloud / "keep.zip").write_bytes(b"same" * 5000)
    monkeypatch.chdir(tmp_path)
    return root


def test_deep_classifies_once_and_writes_only_the_final_plan(tmp_path, monkeypatch, capsys):
    root = scan_fixture(tmp_path, monkeypatch)
    calls = Counter()
    saved = []
    original_store = cli._store_plan

    def counted(record, policy):
        calls[str(record.path)] += 1
        return classify(record, policy)

    def store(plan):
        saved.append(plan)
        original_store(plan)

    for module in (cli, deep, old_files):
        monkeypatch.setattr(module, "classify", counted)
    monkeypatch.setattr(cli, "_store_plan", store)
    assert cli.main(["start", "--mode", "deep", "--run-id", "once",
                     "--summary-only", str(root)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert len(calls) == 4
    assert set(calls.values()) == {1}, calls
    assert len(saved) == 1
    by_name = {c.path.name: c for c in saved[0].candidates}
    assert by_name["b.zip"].category == "duplicate"
    assert "unclassified" not in by_name["b.zip"].evidence["rules"]
    assert by_name["old.pdf"].actionable
    assert "not modified for at least 12 months" in by_name["old.pdf"].reasons
    assert not by_name["keep.zip"].actionable
    assert saved[0].digest == output["plan_digest"]


def test_widget_defers_report_and_emits_progress_for_post_inventory_work(tmp_path, monkeypatch, capsys):
    root = scan_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("FREEUP_WIDGET_PROGRESS", "1")
    assert cli.main(["start", "--mode", "deep", "--run-id", "stages",
                     "--summary-only", str(root)]) == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["report_path"] is None
    assert output["report_deferred"] is True
    assert not cli._report_path("stages").exists()
    events = [json.loads(line.split(" ", 1)[1]) for line in captured.err.splitlines()
              if line.startswith("FREEUP_STAGE ")]
    phases = {e["phase"] for e in events}
    assert {"Saving inventory", "Categorizing files", "Checking duplicates",
            "Preparing results", "Saving results"} <= phases
    categorizing = [e for e in events if e["phase"] == "Categorizing files"]
    assert categorizing[0]["processed"] == 0
    assert categorizing[-1]["processed"] == categorizing[-1]["total"] == 4
    assert all(e["updated_at"] >= e["phase_started_at"] for e in events)
    duplicate_progress = [e for e in events if e["phase"] == "Checking duplicates"]
    # Partial fingerprints and full hashes are separate checks. There is no
    # known total before fingerprints reveal which full hashes are required.
    assert all(e["total"] is None and e["unit"] == "checks" for e in duplicate_progress)
    assert duplicate_progress[-1]["processed"] == 4
    assert cli.main(["report", "--run-id", "stages", "--summary-only"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "# Freeup Space cleanup plan" in open(report["report_path"]).read()


def test_stage_updates_preserve_inventory_counts_and_clear_stale_path(tmp_path, monkeypatch):
    service = WidgetService()
    view = service.open(str(tmp_path))
    session = service.session(view["session_id"])
    session.update(status="scanning", run_id="progress-test",
                   progress={"files_scanned": 100, "bytes_scanned": 2048,
                             "current_path": "/old/inventory/path", "message": "Inventory"})
    event = {"phase": "Categorizing files", "processed": 25, "total": 100,
             "unit": "files", "updated_at": 10, "phase_started_at": 8}
    checkpoint = {"event": "inventory-saved", "run_id": "progress-test"}
    child = Mock(stderr=io.StringIO("FREEUP_EVENT " + json.dumps(checkpoint) + "\n" +
                                   "FREEUP_STAGE " + json.dumps(event) + "\n"),
                 stdout=io.StringIO("{}"), returncode=0)
    monkeypatch.setattr("freeup_space.widget_service.subprocess.Popen", lambda *a, **k: child)
    service.command(session, ["start"], progress=True)
    assert session["progress"]["phase"] == "Categorizing files"
    assert session["progress"]["processed"] == 25
    assert session["progress"]["files_scanned"] == 100
    assert session["progress"]["bytes_scanned"] == 2048
    assert not session["progress"].get("current_path")
    assert session["can_resume"]


def test_duplicate_budget_excludes_singletons_and_empty_files(tmp_path, monkeypatch):
    from test_scan_modes import _record_for
    paths = []
    for name, content in (("huge.bin", b"x" * 100), ("a.bin", b"same"),
                          ("b.bin", b"same"), ("empty", b"")):
        p = tmp_path / name
        p.write_bytes(content)
        paths.append(p)
    monkeypatch.setattr(deep, "_DUPLICATE_MAX_FILES", 2)
    monkeypatch.setattr(deep, "_DUPLICATE_MAX_BYTES", 100)
    selected, coverage = deep._select_duplicate_records(tuple(map(_record_for, paths)))
    assert {r.path.name for r in selected} == {"a.bin", "b.bin"}
    assert coverage["duplicate_selected_bytes"] == 8


@pytest.mark.parametrize("compression_threshold", [1, 1024 * 1024])
def test_interruption_leaves_inventory_resumable_without_a_preliminary_plan(
        tmp_path, monkeypatch, capsys, compression_threshold):
    from freeup_space import json_stream
    monkeypatch.setattr(json_stream, "COMPRESSION_THRESHOLD", compression_threshold)
    root = scan_fixture(tmp_path, monkeypatch)
    original = deep.analyze

    def interrupt(run, **kwargs):
        assert cli._load_run(run.run_id).coverage["inventory_finished"]
        assert not cli._plan_path(run.run_id).exists()
        raise KeyboardInterrupt()

    monkeypatch.setattr(deep, "analyze", interrupt)
    with pytest.raises(SystemExit) as error:
        cli.main(["start", "--mode", "deep", "--run-id", "resume", "--summary-only", str(root)])
    assert error.value.code == 130
    capsys.readouterr()
    monkeypatch.setattr(deep, "analyze", original)
    monkeypatch.setattr(deep, "inventory", lambda *a: pytest.fail("inventory repeated"))
    assert cli.main(["start", "--mode", "deep", "--resume", "resume", "--summary-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["coverage"]["duplicate_groups"] == 1
    assert (root / "a.zip").exists() and (root / "b.zip").exists()


def test_reused_classification_preserves_old_file_hardlink_and_plan_digest(tmp_path, monkeypatch, capsys):
    root = scan_fixture(tmp_path, monkeypatch)
    os.link(root / "old.pdf", root / "z-alias.pdf")
    assert cli.main(["start", "--mode", "deep", "--run-id", "parity", "--summary-only", str(root)]) == 0
    result = json.loads(capsys.readouterr().out)
    run = cli._load_run("parity")
    # Independent legacy collection path pins plan compatibility for saved approvals.
    policy = cli.Policy.for_platform(run.platform)
    old = old_files.find_old_files(run.files, run.completed_at or run.started_at, policy=policy)
    established = {e.path for e in old} | {p for g in run.duplicate_groups for p in g.duplicate_paths}
    evidence = old + [e for r in run.files for e in classify(r, policy)
                      if e.rule != "unclassified" or r.path not in established]
    plan = cli.build_plan(run, evidence, policy)
    assert plan.digest == result["plan_digest"]
    aged = [c.path.name for c in plan.candidates if "old-file-12-months" in c.evidence["rules"]]
    assert aged == ["old.pdf"]


def test_canonical_record_encoding_preserves_digest_format_and_unicode(tmp_path):
    from datetime import datetime, timezone
    from dataclasses import replace
    from pathlib import Path
    from freeup_space.models import Candidate, CandidateSnapshot, CleanupPlan, Risk, ActionType
    from freeup_space.json_stream import canonical_chunks, atomic_json, load_json
    item = Candidate("ARC-001", Path("/synthetic/東京/archive.zip"), "archive", ("review",),
                     Risk.MEDIUM, True, CandidateSnapshot(1, 2, 3, 4.5),
                     evidence={"rules": ("archive-extension",), "bytes": b"abc"},
                     proposed_action=ActionType.TRASH)
    plan = CleanupPlan("1", "canonical", "macos", datetime(2026, 1, 1, tzinfo=timezone.utc),
                       "1", (item,), "")
    expected = json.dumps(plan.to_dict(), ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":"))
    assert "".join(canonical_chunks(plan)) == expected
    atomic_json(tmp_path / "plan.json", plan)
    assert load_json(tmp_path / "plan.json") == json.loads(expected)


def test_category_totals_are_cached_until_the_plan_changes(tmp_path):
    service = WidgetService()
    session = service.session(service.open(str(tmp_path))["session_id"])

    class CountedList(list):
        visits = 0

        def __iter__(self):
            self.visits += 1
            return super().__iter__()

    candidates = CountedList([dict(candidate_id="ARC-001", path="/example.zip", category="archive",
                                  actionable=True, risk="medium", proposed_action="trash", reclaimable_bytes=42)])
    session["plan"] = {"digest": "first", "candidates": candidates}
    assert service.view(session)["reclaimable_bytes"] == 42
    candidates.visits = 0
    for _ in range(3):
        assert service.view(session, category="archive")["categories"][0]["count"] == 1
    assert candidates.visits == 0
    session["plan"] = {"digest": "second", "candidates": []}
    assert service.view(session)["reclaimable_bytes"] == 0


def test_replacing_a_compressed_receipt_preserves_its_format(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from freeup_space import json_stream
    from freeup_space.models import Receipt
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(json_stream, "COMPRESSION_THRESHOLD", 1)
    receipt = Receipt("compressed", "macos", datetime(2026, 1, 1, tzinfo=timezone.utc))
    json_stream.atomic_json(cli._receipt_path("compressed"), receipt)
    assert cli._receipt_path("compressed").suffix == ".gz"
    cli._store_receipt("compressed", receipt)
    assert json_stream.load_json(cli._receipt_path("compressed")) == receipt.to_dict()


def test_failed_artifact_commit_preserves_previous_inventory(tmp_path, monkeypatch):
    import errno
    from freeup_space import json_stream
    path = tmp_path / "run.json"
    previous = {"run_id": "saved", "complete": False}
    json_stream.atomic_json(path, previous)
    monkeypatch.setattr(json_stream, "COMPRESSION_THRESHOLD", 1)

    def out_of_space(*args):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(json_stream.os, "replace", out_of_space)
    with pytest.raises(OSError, match="previous saved artifact was preserved"):
        json_stream.atomic_json(path, {"run_id": "saved", "complete": True})
    assert json_stream.load_json(path) == previous
    assert list(tmp_path.iterdir()) == [path]
