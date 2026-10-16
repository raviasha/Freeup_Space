"""Opening evidence must describe recorded app opens, never filesystem access."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

from freeup_space import cli
from freeup_space.categories import classify
from freeup_space.models import FileRecord, ScanRun
from freeup_space.plans import build_plan
from freeup_space.policy import Policy


NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
OPENED = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()
SOURCE = "macos-launchservices"


def record(path, **changes):
    return replace(FileRecord(path=Path(path), size=100, allocated_size=100,
                              mtime=NOW.timestamp(), atime=NOW.timestamp(),
                              ctime=NOW.timestamp(), st_dev=1, st_ino=1,
                              volume_id="1", file_id="1:1"), **changes)


def run_for(*records, platform="macos"):
    return ScanRun(run_id="opening-test", platform=platform, started_at=NOW,
                   completed_at=NOW, complete=True, mode="deep", files=records,
                   coverage={"inventory_finished": True})


def plan_for(run):
    policy = Policy.for_platform(run.platform)
    return build_plan(run, [item for value in run.files for item in classify(value, policy)], policy)


def test_recorded_opening_survives_run_artifact_and_plan_without_changing_mtime(tmp_path, monkeypatch):
    # Dropping the fields during decode or plan construction loses this evidence.
    monkeypatch.chdir(tmp_path)
    value = record(tmp_path / "report.pdf", last_opened_at=OPENED, last_opened_source=SOURCE)
    run = run_for(value)
    cli._store_run(run)
    saved = cli._load_run(run.run_id)
    candidate = plan_for(saved).candidates[0]
    assert candidate.evidence["opening_history"] == {"last_opened_at": OPENED, "source": SOURCE}
    assert candidate.snapshot.mtime == NOW.timestamp()
    assert cli._encode_run(saved)["files"][0]["last_opened_at"] == OPENED
    assert plan_for(saved).digest == plan_for(run).digest


@pytest.mark.parametrize("timestamp,source", [
    (None, None), (float("nan"), SOURCE), (float("inf"), SOURCE),
    (-1, SOURCE), (0, SOURCE), (True, SOURCE), ("yesterday", SOURCE),
    (NOW.timestamp() + 1, SOURCE), (OPENED, "filesystem-atime"), (OPENED, None), (10 ** 400, SOURCE),
])
def test_invalid_or_untrusted_opening_is_unknown(tmp_path, timestamp, source):
    # Missing validation would promote malformed/future/access times to recorded opens.
    value = record(tmp_path / "report.pdf", last_opened_at=timestamp, last_opened_source=source)
    run = replace(run_for(value), coverage={"inventory_finished": True, "opening_history": {"status": "complete"}})
    candidate = plan_for(run).candidates[0]
    assert candidate.evidence["opening_history"] == {"last_opened_at": None, "source": None}


def test_legacy_records_keep_opening_history_unknown(tmp_path):
    run = run_for(record(tmp_path / "legacy.pdf"))
    encoded = cli._encode_run(run)
    encoded["files"][0].pop("last_opened_at", None)
    encoded["files"][0].pop("last_opened_source", None)
    saved = cli._decode_run(encoded)
    assert "opening_history" not in plan_for(saved).candidates[0].evidence


def test_managed_file_cannot_carry_personal_opening_evidence(tmp_path):
    value = record(tmp_path / "OneDrive" / "report.pdf", last_opened_at=OPENED, last_opened_source=SOURCE)
    candidate = plan_for(run_for(value)).candidates[0]
    assert not candidate.actionable
    assert "opening_history" not in candidate.evidence


def test_enrichment_reads_only_eligible_personal_metadata_and_records_coverage(tmp_path, monkeypatch, capsys):
    from freeup_space import opening_history
    values = [record(tmp_path / "report.pdf"), record(tmp_path / "missing.jpg", st_ino=2),
              record(tmp_path / "OneDrive" / "cloud.pdf"),
              record(tmp_path / "backups" / "backup.pdf"),
              record(tmp_path / "notes.txt"), record(tmp_path / "folder.pdf", file_kind="directory")]

    def native(paths, seconds):
        assert tuple(value.path.name for value in paths) == ("missing.jpg", "report.pdf")
        assert seconds == 2.0
        return {str(tmp_path / "report.pdf"): OPENED, str(tmp_path / "missing.jpg"): None}, "complete"

    monkeypatch.setattr(opening_history, "_read_metadata", native, raising=False)
    monkeypatch.setenv("FREEUP_WIDGET_PROGRESS", "1")
    enriched = opening_history.enrich_opening_history(run_for(*values), max_seconds=2.0)
    known = next(value for value in enriched.files if value.path.name == "report.pdf")
    assert known.last_opened_at == OPENED and known.last_opened_source == SOURCE
    assert all(value.last_opened_at is None for value in enriched.files if value != known)
    coverage = enriched.coverage["opening_history"]
    assert coverage["eligible_files"] == 2
    assert coverage["checked_files"] == 2
    assert coverage["known_files"] == 1
    assert coverage["unknown_files"] == 1
    assert coverage["status"] == "complete"
    assert "Reading opening history" in capsys.readouterr().err


def test_item_budget_leaves_unchecked_opening_history_unknown(tmp_path, monkeypatch):
    from freeup_space import opening_history

    def native(paths, seconds):
        return {str(value.path): OPENED for value in paths}, "complete"

    monkeypatch.setattr(opening_history, "_read_metadata", native, raising=False)
    run = run_for(*(record(tmp_path / name) for name in ("a.pdf", "b.pdf", "c.pdf")))
    enriched = opening_history.enrich_opening_history(run, max_files=2)
    assert [value.last_opened_at for value in enriched.files] == [OPENED, OPENED, None]
    coverage = enriched.coverage["opening_history"]
    assert coverage["checked_files"] == 2
    assert coverage["unknown_files"] == 1
    assert coverage["budget_exhausted"] is True


@pytest.mark.parametrize("platform,seconds,status", [("macos", 0, "budget-exhausted"), ("windows", 2, "unavailable")])
def test_zero_budget_and_unsupported_platform_do_no_native_work(tmp_path, monkeypatch, platform, seconds, status):
    from freeup_space import opening_history
    monkeypatch.setattr(opening_history, "_read_metadata", lambda *a: pytest.fail("unexpected native metadata read"), raising=False)
    run = run_for(record(tmp_path / "unknown.pdf"), platform=platform)
    enriched = opening_history.enrich_opening_history(run, max_seconds=seconds)
    assert enriched.files[0].last_opened_at is None
    assert enriched.coverage["opening_history"]["status"] == status
    assert enriched.coverage["opening_history"]["checked_files"] == 0


def test_native_deadline_retains_finished_metadata_and_terminates_stalled_worker(tmp_path, monkeypatch):
    import sys
    import time
    from freeup_space import opening_history, runtime
    # Replace only the native worker with a deterministic local metadata provider.
    script = ('import sys,json,time; paths=json.load(sys.stdin); '
              'print(json.dumps([paths[0]["path"],1704067200.0]),flush=True); time.sleep(30)')
    monkeypatch.setattr(runtime, "cli_command", lambda args: [sys.executable, "-u", "-c", script])
    started = time.monotonic()
    metadata, status = opening_history._read_metadata((record(tmp_path / "a.pdf"), record(tmp_path / "b.pdf")), 0.5)
    assert metadata == {str(tmp_path / "a.pdf"): OPENED}
    assert status == "budget-exhausted"
    assert time.monotonic() - started < 5


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS opening collection pipeline")
def test_deep_checkpoint_persists_openings_before_analysis_and_resume_never_rereads(tmp_path, monkeypatch, capsys):
    from freeup_space import opening_history, deep
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "files"
    root.mkdir()
    (root / "report.pdf").write_bytes(b"synthetic pdf metadata fixture")
    monkeypatch.setattr(opening_history, "_read_metadata", lambda paths, seconds: ({str(value.path): OPENED for value in paths}, "complete"), raising=False)
    analyze = deep.analyze

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(deep, "analyze", interrupted)
    with pytest.raises(SystemExit) as error:
        cli.main(["start", "--mode", "deep", "--run-id", "opening-checkpoint", str(root)])
    assert error.value.code == 130
    saved = cli._load_run("opening-checkpoint")
    assert not saved.complete
    assert saved.files[0].last_opened_at == OPENED
    monkeypatch.setattr(deep, "analyze", analyze)
    monkeypatch.setattr(opening_history, "_read_metadata", lambda *a: pytest.fail("saved run reread live opening metadata"), raising=False)
    assert cli.main(["start", "--mode", "deep", "--resume", saved.run_id, "--summary-only"]) == 0
    completed = cli._load_run(saved.run_id)
    before = plan_for(completed).digest
    assert cli.main(["report", "--run-id", saved.run_id, "--summary-only"]) == 0
    assert plan_for(cli._load_run(saved.run_id)).digest == before
    assert completed.files[0].last_opened_source == SOURCE


def test_worker_records_missing_and_failed_native_metadata_as_unknown(tmp_path, monkeypatch, capsys):
    import io
    import json
    import sys
    from freeup_space import opening_history
    paths = [tmp_path / "known.pdf", tmp_path / "missing.pdf", tmp_path / "failed.pdf"]

    class Native:
        def read(self, path):
            if path.name == "failed.pdf":
                raise OSError("unavailable")
            return OPENED if path.name == "known.pdf" else None

    monkeypatch.setattr(opening_history, "_MacOSLastOpened", Native, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    values = []
    for path in paths:
        path.write_bytes(b"synthetic metadata")
        stat = path.stat()
        values.append({"path": str(path), "st_dev": stat.st_dev, "st_ino": stat.st_ino,
                       "size": stat.st_size, "mtime": stat.st_mtime})
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(values)))
    assert cli.main(["_opening-history-worker"]) == 0
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        [str(paths[0]), OPENED], [str(paths[1]), None], [str(paths[2]), None],
    ]


def test_metadata_worker_uses_frozen_runtime_command_and_utf8(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    from freeup_space import opening_history
    path = tmp_path / "café.pdf"
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    class Child:
        returncode = 0

        def __init__(self, command, **options):
            assert command == [sys.executable, "--cli", "_opening-history-worker"]
            assert options["stdin"] == subprocess.PIPE
            assert options["encoding"] == "utf-8"

        def communicate(self, data=None, timeout=None):
            assert json.loads(data) == [{"path": str(path), "st_dev": 1, "st_ino": 1,
                                              "size": 100, "mtime": NOW.timestamp()}]
            return json.dumps([str(path), OPENED]) + "\n", ""

    monkeypatch.setattr(subprocess, "Popen", Child)
    metadata, status = opening_history._read_metadata((record(path),), 2.0)
    assert metadata == {str(path): OPENED}
    assert status == "complete"


def test_native_boundary_returns_unknown_for_nonexistent_fixture(tmp_path):
    import sys
    from freeup_space import opening_history
    if sys.platform != "darwin":
        pytest.skip("CoreServices is macOS only")
    assert opening_history._MacOSLastOpened().read(tmp_path / "absent.pdf") is None


@pytest.mark.parametrize("when", ["before", "during", "symlink"])
def test_worker_rejects_metadata_when_inventory_identity_changes(tmp_path, monkeypatch, capsys, when):
    import io
    import json
    import sys
    from freeup_space import opening_history
    path = tmp_path / "changed.pdf"
    path.write_bytes(b"old fixture")
    snapshot = path.stat()
    payload = [{"path": str(path), "st_dev": snapshot.st_dev, "st_ino": snapshot.st_ino,
                "size": snapshot.st_size, "mtime": snapshot.st_mtime}]

    def replace_file():
        path.unlink()
        if when == "symlink":
            target = tmp_path / "target.pdf"
            target.write_bytes(b"new fixture")
            path.symlink_to(target)
        else:
            path.write_bytes(b"replacement has changed size")

    class Native:
        def read(self, item):
            if when == "during":
                replace_file()
            return OPENED

    if when in {"before", "symlink"}:
        replace_file()
    monkeypatch.setattr(opening_history, "_MacOSLastOpened", Native)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["_opening-history-worker"]) == 0
    assert json.loads(capsys.readouterr().out) == [str(path), None]


@pytest.mark.parametrize("value", [None, NOW.timestamp() + 86400 * 36500, float("inf"), "invalid"])
def test_invalid_native_values_remain_unknown_in_inventory(tmp_path, monkeypatch, value):
    from freeup_space import opening_history
    item = record(tmp_path / "unknown.pdf")
    monkeypatch.setattr(opening_history, "_read_metadata", lambda *a: ({str(item.path): value}, "complete"))
    run = opening_history.enrich_opening_history(run_for(item))
    assert run.files[0].last_opened_at is None
    assert run.files[0].last_opened_source is None
    assert run.coverage["opening_history"]["known_files"] == 0
    assert run.coverage["opening_history"]["unknown_files"] == 1


def test_legacy_reviewed_plan_rebuild_preserves_digest_and_dry_run(tmp_path, monkeypatch, capsys):
    import hashlib
    import json
    from freeup_space.models import Candidate, CandidateSnapshot, CleanupPlan, Risk
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "legacy.pdf"
    path.write_bytes(b"synthetic legacy document")
    state = path.stat()
    item = record(path, size=state.st_size, allocated_size=state.st_size,
                  mtime=state.st_mtime, atime=state.st_atime, ctime=state.st_ctime,
                  st_dev=state.st_dev, st_ino=state.st_ino)
    run = run_for(item)
    old_payload = cli._encode_run(run)
    old_payload["files"][0].pop("last_opened_at")
    old_payload["files"][0].pop("last_opened_source")
    cli._atomic_write(cli._run_path(run.run_id), json.dumps(old_payload))
    # This is the reviewed pre-feature plan shape, constructed independently of
    # build_plan so adding evidence to a legacy rebuild changes its signature.
    candidate = Candidate(
        candidate_id="PER-001", path=path, display_path=str(path), category="personal-data",
        reasons=("valuable user data",), risk=Risk.HIGH, actionable=True,
        snapshot=CandidateSnapshot(st_dev=state.st_dev, st_ino=state.st_ino,
                                   size=state.st_size, mtime=state.st_mtime, path=path),
        volume_id="1", file_id="1:1", size=state.st_size, allocated_size=state.st_size,
        file_kind="regular", evidence={"categories": ("personal-data",), "rules": ("personal-media-extension",)},
        reclaimable_bytes=state.st_size, created_at=NOW,
    )
    reviewed = CleanupPlan(schema_version="1", run_id=run.run_id, platform="macos", created_at=NOW,
                           policy_version="1", candidates=(candidate,), digest="")
    reviewed = replace(reviewed, digest=hashlib.sha256(json.dumps(
        reviewed.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest())
    cli._store_plan(reviewed)
    rebuilt = cli._build_plan(cli._load_run(run.run_id), "macos")
    assert rebuilt.digest == reviewed.digest
    assert cli.main(["apply", "--run-id", run.run_id, "--dry-run", "PER-001"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["plan_digest"] == reviewed.digest
    assert receipt["skipped"] == [{"candidate_id": "PER-001", "path": str(path), "reason": "dry-run"}]
    assert receipt["moved"] == [] and receipt["failed"] == []
    assert path.read_bytes() == b"synthetic legacy document"


def test_new_deep_coverage_persists_explicit_unknown_opening_evidence(tmp_path):
    run = replace(run_for(record(tmp_path / "unknown.pdf")),
                  coverage={"inventory_finished": True, "opening_history": {"status": "unavailable"}})
    assert plan_for(run).candidates[0].evidence["opening_history"] == {"last_opened_at": None, "source": None}
