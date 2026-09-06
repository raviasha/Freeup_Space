import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from freeup_space.models import Candidate, CandidateSnapshot, Risk
from freeup_space.policy import Policy
from freeup_space.safety import is_protected


def test_windows_system_roots_are_report_only():
    decision = is_protected(
        Path(r"C:\Windows\System32\config"),
        Policy.for_platform("windows"),
        "windows",
    )

    assert decision.actionable is False
    assert decision.outcome == "report-only"
    assert decision.reason


def test_windows_protected_path_matching_is_case_insensitive():
    decision = is_protected(
        Path(r"c:\WINDOWS\Temp\update.bin"),
        Policy.for_platform("WINDOWS"),
        "windows",
    )

    assert decision.actionable is False
    assert decision.outcome == "report-only"


def test_macos_path_is_normalized_without_following_links():
    decision = is_protected(
        Path("/Users/example/../..//System/Library/cache"),
        Policy.for_platform("macos"),
        "macos",
    )

    assert decision.actionable is False
    assert decision.outcome == "report-only"


def test_relative_macos_path_is_checked_at_its_lexical_absolute_location(
    tmp_path, monkeypatch
):
    protected = tmp_path / "protected"
    protected.mkdir()
    policy = replace(
        Policy.for_platform("macos"), protected_roots=(str(protected),)
    )
    monkeypatch.chdir(tmp_path)

    decision = is_protected(Path("protected/item.bin"), policy, "macos")

    assert decision.actionable is False
    assert decision.outcome == "report-only"


def test_unprotected_path_is_allowed():
    decision = is_protected(
        Path("/Users/example/Downloads/archive.zip"),
        Policy.for_platform("macos"),
        "macos",
    )

    assert decision.actionable is True
    assert decision.outcome == "allow"


def test_windows_volume_root_is_rejected():
    decision = is_protected(
        Path("C:\\"), Policy.for_platform("windows"), "windows"
    )

    assert decision.actionable is False
    assert decision.outcome == "reject"


def test_policy_rejects_unsupported_platform():
    with pytest.raises(ValueError, match="Unsupported platform"):
        Policy.for_platform("linux")


def test_candidate_serialization_is_json_safe():
    snapshot = CandidateSnapshot(
        st_dev=1,
        st_ino=2,
        size=3,
        mtime=4.5,
        digest="abc",
        path=Path("/tmp/item.bin"),
    )
    candidate = Candidate(
        candidate_id="OLD-001",
        path=Path("/tmp/item.bin"),
        category="old-file",
        reasons=("unchanged for 13 months",),
        risk=Risk.MEDIUM,
        actionable=True,
        snapshot=snapshot,
        created_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
    )

    encoded = json.dumps(candidate.to_dict())

    assert '"risk": "medium"' in encoded
    assert '"path": "/tmp/item.bin"' in encoded
    assert '"created_at": "2026-09-06T00:00:00+00:00"' in encoded
