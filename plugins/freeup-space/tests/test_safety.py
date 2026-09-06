import os
from dataclasses import replace
from pathlib import Path
from typing import Optional

from freeup_space.models import CandidateSnapshot
from freeup_space.policy import Policy
from freeup_space.safety import validate_target


def snapshot_for(path: Path, *, safe_root: Optional[Path] = None) -> CandidateSnapshot:
    stat = path.lstat()
    return CandidateSnapshot(
        st_dev=stat.st_dev,
        st_ino=stat.st_ino,
        size=stat.st_size,
        mtime=stat.st_mtime,
        path=path,
        safe_root=safe_root,
    )


def test_symlink_target_is_rejected(tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("x")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    decision = validate_target(
        link, Policy.for_platform("macos"), snapshot_for(link)
    )

    assert decision.actionable is False
    assert decision.outcome == "reject"
    assert "link" in decision.reason.lower()


def test_target_outside_recorded_safe_root_is_rejected(tmp_path):
    safe_root = tmp_path / "cache"
    safe_root.mkdir()
    target = tmp_path / "outside.bin"
    target.write_bytes(b"x")

    decision = validate_target(
        target,
        Policy.for_platform("macos"),
        snapshot_for(target, safe_root=safe_root),
    )

    assert decision.actionable is False
    assert decision.outcome == "reject"
    assert "outside" in decision.reason.lower()


def test_changed_file_identity_is_rejected(tmp_path):
    target = tmp_path / "candidate.bin"
    target.write_bytes(b"original")
    snapshot = snapshot_for(target)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"replacement")
    os.replace(replacement, target)

    decision = validate_target(target, Policy.for_platform("macos"), snapshot)

    assert decision.actionable is False
    assert decision.outcome == "reject"
    assert "snapshot" in decision.reason.lower()


def test_unchanged_regular_file_is_allowed(tmp_path):
    target = tmp_path / "candidate.bin"
    target.write_bytes(b"content")

    decision = validate_target(
        target,
        Policy.for_platform("macos"),
        snapshot_for(target, safe_root=tmp_path),
    )

    assert decision.actionable is True
    assert decision.outcome == "allow"


def test_missing_target_is_rejected(tmp_path):
    target = tmp_path / "missing.bin"
    snapshot = CandidateSnapshot(st_dev=1, st_ino=2, size=3, mtime=4.0, path=target)

    decision = validate_target(target, Policy.for_platform("macos"), snapshot)

    assert decision.actionable is False
    assert decision.outcome == "reject"
    assert "missing" in decision.reason.lower()


def test_workspace_root_is_rejected_even_when_snapshot_matches(tmp_path):
    policy = replace(
        Policy.for_platform("macos"), workspace_roots=(str(tmp_path),)
    )

    decision = validate_target(tmp_path, policy, snapshot_for(tmp_path))

    assert decision.actionable is False
    assert decision.outcome == "reject"
    assert "workspace" in decision.reason.lower()
