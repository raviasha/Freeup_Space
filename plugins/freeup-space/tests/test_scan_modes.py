import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from freeup_space import cli
from freeup_space import deep
from freeup_space.investigation import validate_notes
from freeup_space.models import FileRecord
from freeup_space.plans import PlanError, select_ids
from freeup_space.policy import Policy
from freeup_space.revalidate import revalidate


PLATFORM = "windows" if os.name == "nt" else "macos"


@pytest.fixture
def fixture_tree(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "files"
    root.mkdir()
    (root / "a.txt").write_text("unique private contents" * 1000)
    (root / "b.txt").write_bytes((root / "a.txt").read_bytes())
    (root / "different.txt").write_text("different" * 1000)
    old = root / "old.pdf"
    old.write_bytes(b"old report" * 2000)
    timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old, (timestamp, timestamp))
    cloud = root / "OneDrive"
    cloud.mkdir()
    (cloud / "cloud-copy.txt").write_bytes((root / "a.txt").read_bytes())
    monkeypatch.chdir(workspace)
    return root


def start(root, mode, capsys, run_id="test-run"):
    assert cli.main(["start", "--mode", mode, "--run-id", run_id,
                     "--summary-only", str(root)]) == 0
    return json.loads(capsys.readouterr().out)


def _record_for(path: Path) -> FileRecord:
    value = path.stat()
    return FileRecord(
        path=path,
        size=value.st_size,
        allocated_size=getattr(value, "st_blocks", 0) * 512,
        mtime=value.st_mtime,
        atime=value.st_atime,
        ctime=value.st_ctime,
        st_dev=value.st_dev,
        st_ino=value.st_ino,
        volume_id=str(value.st_dev),
        file_id="{}:{}".format(value.st_dev, value.st_ino),
    )


def test_no_mode_shows_all_choices_without_scanning(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "cmd_quick", lambda _: pytest.fail("unexpected scan"))
    for argv in ([], ["start"], ["modes"]):
        assert cli.main(argv) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "choose-mode"
        assert [mode["id"] for mode in result["modes"]] == ["quick", "deep", "deep-ai"]
    assert not cli.RUNS_DIR.exists()


def test_deep_persists_real_duplicates_old_files_and_managed_findings(fixture_tree, capsys):
    output = start(fixture_tree, "deep", capsys)
    run = cli._load_run("test-run")
    assert run.complete and run.mode == "deep"
    assert len(run.duplicate_groups) == 1
    assert run.duplicate_groups[0].duplicate_paths == (fixture_tree / "b.txt",)
    assert output["coverage"]["old_file_months"] == 12
    plan = cli._build_plan(run, PLATFORM)
    by_name = {c.path.name: c for c in plan.candidates}
    assert by_name["b.txt"].category == "duplicate"
    assert by_name["b.txt"].actionable
    assert "not modified for at least 12 months" in by_name["old.pdf"].reasons
    assert not by_name["cloud-copy.txt"].actionable
    assert not (cli.RUNS_DIR / "test-run" / "investigation.json").exists()
    assert not any("content" in key for key in output["candidates"][0])
    assert "- [ ]" in Path(output["report_path"]).read_text()
    assert (fixture_tree / "b.txt").exists()


def test_duplicate_selection_has_hard_file_and_byte_budgets(tmp_path, monkeypatch):
    paths = []
    for name in ("a.bin", "b.bin", "c.bin"):
        path = tmp_path / name
        path.write_bytes(b"data")
        paths.append(path)
    monkeypatch.setattr(deep, "_DUPLICATE_MAX_FILES", 2)
    monkeypatch.setattr(deep, "_DUPLICATE_MAX_BYTES", 8)

    selected, coverage = deep._select_duplicate_records(
        tuple(_record_for(path) for path in paths)
    )

    assert tuple(record.path.name for record in selected) == ("a.bin", "b.bin")
    assert coverage["duplicate_selected_files"] == 2
    assert coverage["duplicate_selected_bytes"] == 8
    assert coverage["duplicate_selection_limited"] is True


def test_duplicate_timeout_finalizes_a_safe_nonduplicate_result(fixture_tree):
    run = deep.inventory((fixture_tree,), PLATFORM, "timeout-run", "deep")

    result = deep.analyze(run, max_seconds=0)

    assert result.complete
    assert result.duplicate_groups == ()
    assert result.coverage["duplicates"] == "incomplete"
    assert result.coverage["analysis"] == "duplicate-time-budget-exhausted"


def test_saved_report_is_repeatable_and_does_not_rescan(fixture_tree, capsys, monkeypatch):
    output = start(fixture_tree, "deep", capsys)
    before = Path(output["report_path"]).read_bytes()
    monkeypatch.setattr(cli, "scan_paths", lambda *a, **kw: pytest.fail("report rescanned"))
    cli.main(["report", "--run-id", "test-run", "--summary-only"])
    assert Path(output["report_path"]).read_bytes() == before


def test_ai_packet_has_metadata_only_and_notes_cannot_change_plan(fixture_tree, capsys):
    output = start(fixture_tree, "deep-ai", capsys)
    packet = json.loads(Path(output["ai_investigation"]["packet_path"]).read_text())
    assert packet["file_contents_included"] is False
    assert "unique private contents" not in json.dumps(packet)
    plan_path = cli.RUNS_DIR / "test-run" / "plan.json"
    plan_before = plan_path.read_bytes()
    data = {"run_id": "test-run", "plan_digest": packet["plan_digest"], "findings": [{
        "finding_id": packet["findings"][0]["finding_id"],
        "assessment": "needs review", "evidence": "metadata only", "uncertainty": "ownership unknown",
        "suggested_action": "review-files", "actionable": True,
    }]}
    notes = Path("notes.json")
    notes.write_text(json.dumps(data))
    assert cli.main(["record-investigation", "--run-id", "test-run", "--input", str(notes)]) == 0
    saved = json.loads((cli.RUNS_DIR / "test-run" / "ai-notes.json").read_text())
    assert saved["findings"][0]["actionable"] is False
    assert saved["findings"][0]["status"] == "ai-suggestion"
    assert plan_path.read_bytes() == plan_before
    data["findings"][0]["suggested_action"] = "delete"
    with pytest.raises(ValueError, match="advisory"):
        validate_notes(packet, data)
    data["plan_digest"] = "changed"
    with pytest.raises(ValueError, match="digest"):
        validate_notes(packet, data)


def test_plain_deep_cannot_silently_enable_ai(fixture_tree, capsys):
    start(fixture_tree, "deep", capsys)
    with pytest.raises(SystemExit) as error:
        cli.main(["investigate", "--run-id", "test-run"])
    assert error.value.code == 2


def test_partial_coverage_keeps_error_details_and_only_individual_files(fixture_tree, capsys, monkeypatch):
    from freeup_space.scanner import FilesystemAdapter
    original = FilesystemAdapter.scan_directory

    def deny_cloud(self, path, expected):
        if path.name == "OneDrive":
            raise PermissionError("synthetic inaccessible folder")
        return original(self, path, expected)

    monkeypatch.setattr(FilesystemAdapter, "scan_directory", deny_cloud)
    output = start(fixture_tree, "deep", capsys)
    run = cli._load_run("test-run")
    assert run.complete  # analysis finished, not a claim of complete coverage
    assert run.coverage["status"] == "partial"
    assert run.coverage["error_count"] == 1
    assert run.errors[0].error_type == "PermissionError"
    assert all(record.file_kind == "regular" for record in run.files)
    assert not any(record.path.name == "cloud-copy.txt" for record in run.files)
    assert "partial" in Path(output["report_path"]).read_text()


def test_changed_reviewed_plan_blocks_apply(fixture_tree, capsys):
    start(fixture_tree, "deep", capsys)
    path = cli.RUNS_DIR / "test-run" / "plan.json"
    stored = json.loads(path.read_text())
    stored["digest"] = "changed"
    path.write_text(json.dumps(stored))
    with pytest.raises(SystemExit) as error:
        cli.main(["apply", "--run-id", "test-run", "--dry-run", "DUP-001"])
    assert error.value.code == 2
    assert (fixture_tree / "b.txt").exists()


def test_duplicate_keeper_must_still_exist_and_not_be_selected(fixture_tree, capsys):
    start(fixture_tree, "deep", capsys)
    plan = cli._build_plan(cli._load_run("test-run"), PLATFORM)
    duplicate = next(c for c in plan.candidates if c.category == "duplicate")
    retained = next(c for c in plan.candidates if str(c.path) == duplicate.evidence["retained_path"])
    # Make the retained record selectable solely to exercise cross-candidate selection.
    from freeup_space.models import Risk
    selectable_keeper = replace(retained, actionable=True, risk=Risk.MEDIUM)
    edited = replace(plan, candidates=tuple(selectable_keeper if c == retained else c for c in plan.candidates))
    with pytest.raises(PlanError, match="retained"):
        select_ids(edited, [duplicate.candidate_id, retained.candidate_id])
    retained.path.rename(retained.path.with_suffix(".moved"))
    assert not revalidate(duplicate, Policy.for_platform(PLATFORM)).actionable


def test_resume_reuses_finished_inventory(fixture_tree, capsys, monkeypatch):
    start(fixture_tree, "deep", capsys)
    run = cli._load_run("test-run")
    cli._store_run(replace(run, complete=False, duplicate_groups=()))
    monkeypatch.setattr("freeup_space.deep.inventory", lambda *a, **kw: pytest.fail("resume rescanned"))
    assert cli.main(["start", "--mode", "deep", "--resume", "test-run", "--summary-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["coverage"]["duplicate_groups"] == 1


def test_run_ids_cannot_escape_and_existing_runs_are_not_overwritten(fixture_tree, capsys):
    with pytest.raises(cli.RunArtifactError):
        cli._run_dir("../outside")
    start(fixture_tree, "deep", capsys)
    before = (cli.RUNS_DIR / "test-run" / "run.json").read_bytes()
    with pytest.raises(SystemExit):
        cli.main(["start", "--mode", "deep", "--run-id", "test-run", str(fixture_tree)])
    assert (cli.RUNS_DIR / "test-run" / "run.json").read_bytes() == before


def test_portable_launcher_works_without_installed_package(tmp_path):
    launcher = Path(__file__).resolve().parents[1] / "scripts" / "freeup_space.py"
    result = subprocess.run([sys.executable, str(launcher)], cwd=tmp_path, capture_output=True, text=True,
                            env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "choose-mode"


@pytest.mark.skipif(os.name != "nt", reason="native Windows launcher")
def test_windows_cmd_launcher(tmp_path):
    launcher = Path(__file__).resolve().parents[1] / "scripts" / "freeup-space.cmd"
    result = subprocess.run([str(launcher), "modes"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "choose-mode"
