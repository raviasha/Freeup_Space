import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path, PureWindowsPath
from uuid import UUID

import pytest

from freeup_space.models import (
    ActionType,
    Candidate,
    CandidateSnapshot,
    CleanupPlan,
    DuplicateGroup,
    Receipt,
    Risk,
    ScanRun,
)
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


def test_windows_system_roots_are_protected_on_non_c_drive():
    decision = is_protected(
        Path(r"D:\Windows\System32\drivers\etc"),
        Policy.for_platform("windows"),
        "windows",
    )

    assert decision.actionable is False
    assert decision.outcome == "report-only"


@pytest.mark.parametrize(
    "path",
    [
        Path(r"\\?\C:\Users\example\Downloads\file.bin"),
        Path(r"\\.\C:\Users\example\Downloads\file.bin"),
        Path(r"\??\C:\Users\example\Downloads\file.bin"),
    ],
)
def test_windows_device_namespace_is_rejected(path):
    decision = is_protected(path, Policy.for_platform("windows"), "windows")

    assert decision.actionable is False
    assert decision.outcome == "reject"


@pytest.mark.parametrize(
    "root",
    ["/usr", "/bin", "/sbin", "/private/etc", "/private/var/log", "/dev"],
)
def test_major_macos_os_roots_are_report_only(root):
    decision = is_protected(
        Path(root) / "nested" / "item", Policy.for_platform("macos"), "macos"
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
    assert json.loads(encoded)["path"] == str(candidate.path)
    assert '"created_at": "2026-09-06T00:00:00+00:00"' in encoded


def test_all_domain_dataclasses_serialize_broad_fields_to_strict_json():
    created_at = datetime(2026, 9, 6, tzinfo=timezone.utc)
    snapshot = CandidateSnapshot(
        st_dev=1,
        st_ino=2,
        size=3,
        mtime=4.5,
        digest="abc",
        path=PureWindowsPath(r"D:\data\item.bin"),
    )
    candidate = Candidate(
        candidate_id="OLD-001",
        path=Path("/tmp/item.bin"),
        category="old-file",
        reasons=("old",),
        risk=Risk.MEDIUM,
        actionable=True,
        snapshot=snapshot,
        evidence={
            "bytes": b"\xff",
            "decimal": Decimal("1.25"),
            "error": ValueError("blocked"),
            "ids": {UUID("12345678-1234-5678-1234-567812345678")},
            "not-a-number": float("nan"),
        },
        created_at=created_at,
    )
    values = {
        "CandidateSnapshot": snapshot,
        "Candidate": candidate,
        "DuplicateGroup": DuplicateGroup(
            group_id="DUP-001",
            digest="abc",
            size=3,
            retained_path=Path("/tmp/keep.bin"),
            duplicate_paths=(Path("/tmp/copy.bin"),),
        ),
        "ScanRun": ScanRun(
            run_id="run-1",
            platform="macos",
            started_at=created_at,
            errors=(PermissionError("denied"),),
        ),
        "CleanupPlan": CleanupPlan(
            schema_version="1",
            run_id="run-1",
            platform="macos",
            created_at=created_at,
            policy_version="1",
            candidates=(candidate,),
            digest="plan-digest",
            action=ActionType.TRASH,
        ),
        "Receipt": Receipt(
            run_id="run-1",
            platform="macos",
            created_at=created_at,
            moved=(Path("/tmp/item.bin"),),
            skipped=(ValueError("changed"),),
        ),
    }

    for name, value in values.items():
        encoded = json.dumps(value.to_dict(), allow_nan=False)
        assert isinstance(encoded, str), name
