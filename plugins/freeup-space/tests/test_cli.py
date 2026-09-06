from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import json
import pytest

from freeup_space.cli import (
    RUNS_DIR,
    RunArtifactError,
    _atomic_write,
    _decode_run,
    _encode_run,
    _load_run,
    build_parser,
    cmd_apply,
    cmd_permanent_delete,
    cmd_report,
    cmd_scan,
)
from freeup_space.models import FileRecord, ScanRun


def _sample_run(tmp_path: Path, *, complete: bool = True) -> ScanRun:
    path = tmp_path / "scan" / "candidate.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"payload")
    stat = path.lstat()
    return ScanRun(
        run_id="run-123",
        platform="macos",
        started_at=datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc),
        completed_at=(
            datetime(2026, 9, 6, 12, 1, tzinfo=timezone.utc) if complete else None
        ),
        complete=complete,
        files=(
            FileRecord(
                path=path,
                size=stat.st_size,
                allocated_size=stat.st_size,
                mtime=stat.st_mtime,
                atime=stat.st_atime,
                ctime=stat.st_ctime,
                st_dev=stat.st_dev,
                st_ino=stat.st_ino,
                volume_id="vol-a",
                file_id="{}:{}".format(stat.st_dev, stat.st_ino),
            ),
        ),
    )


def test_atomic_run_persistence_is_all_or_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = RUNS_DIR / "run-123" / "run.json"

    _atomic_write(target, "{\"ok\": true}\n")

    assert target.read_text(encoding="utf-8") == "{\"ok\": true}\n"
    assert not list(target.parent.glob("*.tmp-*"))


def test_scan_command_persists_a_run_atomically(tmp_path, monkeypatch):
    run = _sample_run(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "freeup_space.cli.scan_paths",
        lambda *args, **kwargs: run,
    )

    exit_code = cmd_scan(
        SimpleNamespace(paths=[Path("one")], platform="macos", run_id="run-123")
    )

    assert exit_code == 0
    stored = _load_run("run-123")
    assert stored.run_id == "run-123"
    assert (RUNS_DIR / "run-123" / "run.json").exists()


def test_report_rejects_incomplete_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _atomic_write(
        RUNS_DIR / "run-1" / "run.json",
        json.dumps(_encode_run(_sample_run(tmp_path, complete=False))),
    )

    with pytest.raises(RunArtifactError, match="incomplete run"):
        cmd_report(SimpleNamespace(run_id="run-1", platform="macos"))


def test_report_rejects_corrupt_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _atomic_write(RUNS_DIR / "run-1" / "run.json", "{not-json")

    with pytest.raises(RunArtifactError, match="corrupt run artifact"):
        _load_run("run-1")


def test_apply_requires_explicit_ids_and_supports_dry_run(tmp_path, monkeypatch):
    run = _sample_run(tmp_path)
    monkeypatch.chdir(tmp_path)
    _atomic_write(RUNS_DIR / "run-7" / "run.json", json.dumps(_encode_run(run)))

    captured = {}

    def fake_build_plan(*args, **kwargs):
        return SimpleNamespace(
            run_id="run-7",
            digest="d" * 64,
            candidates=(),
            action=SimpleNamespace(value="trash"),
            to_dict=lambda: {"run_id": "run-7"},
        )

    def fake_apply_selection(plan, ids, platform, dry_run=False):
        captured["ids"] = tuple(ids)
        captured["dry_run"] = dry_run
        return SimpleNamespace(
            to_dict=lambda: {"approved_ids": list(ids), "dry_run": dry_run}
        )

    monkeypatch.setattr("freeup_space.cli._build_plan", fake_build_plan)
    monkeypatch.setattr("freeup_space.cli.apply_selection", fake_apply_selection)

    with pytest.raises(RunArtifactError, match="explicit candidate IDs"):
        cmd_apply(SimpleNamespace(run_id="run-7", platform="macos", dry_run=True, ids=[]))

    exit_code = cmd_apply(
        SimpleNamespace(
            run_id="run-7",
            platform="macos",
            dry_run=True,
            ids=["OLD-001"],
        )
    )

    assert exit_code == 0
    assert captured == {"ids": ("OLD-001",), "dry_run": True}


def test_permanent_delete_requires_explicit_ids(tmp_path, monkeypatch):
    run = _sample_run(tmp_path)
    monkeypatch.chdir(tmp_path)
    _atomic_write(RUNS_DIR / "run-8" / "run.json", json.dumps(_encode_run(run)))

    monkeypatch.setattr(
        "freeup_space.cli._build_plan",
        lambda *args, **kwargs: SimpleNamespace(
            run_id="run-8",
            digest="e" * 64,
            candidates=(),
            action=SimpleNamespace(value="permanent-delete"),
            to_dict=lambda: {"run_id": "run-8"},
        ),
    )
    monkeypatch.setattr(
        "freeup_space.cli.permanently_delete",
        lambda plan, ids, platform: SimpleNamespace(
            to_dict=lambda: {"approved_ids": list(ids), "platform": platform}
        ),
    )

    with pytest.raises(RunArtifactError, match="explicit candidate IDs"):
        cmd_permanent_delete(
            SimpleNamespace(run_id="run-8", platform="macos", ids=[])
        )


def test_parser_exposes_all_cli_commands():
    parser = build_parser()
    commands = parser._subparsers._group_actions[0].choices

    assert {"scan", "report", "apply", "permanent-delete"} <= set(commands)
