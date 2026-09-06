from dataclasses import replace
from pathlib import Path

from freeup_space.categories import classify
from freeup_space.models import FileRecord, Risk
from freeup_space.policy import Policy


def make_record(path: Path, *, size: int = 20_000) -> FileRecord:
    return FileRecord(
        path=path,
        size=size,
        allocated_size=size,
        mtime=1.0,
        atime=1.0,
        ctime=1.0,
        st_dev=1,
        st_ino=2,
        volume_id="1",
        file_id="1:2",
    )


def test_classify_returns_every_matching_reason_with_highest_risk_first(tmp_path):
    downloads = tmp_path / "Downloads"
    policy = replace(
        Policy.for_platform("macos"), safe_roots=(str(downloads),)
    )
    record = make_record(downloads / "holiday-photo.jpg")

    evidence = classify(record, policy)

    assert {item.rule for item in evidence} == {
        "download-location",
        "personal-media-extension",
    }
    assert evidence[0].risk is Risk.HIGH
    assert all(item.risk is Risk.HIGH for item in evidence)
    assert all(item.path == record.path for item in evidence)


def test_known_cache_and_log_rules_are_additive_inside_explicit_safe_root(tmp_path):
    cache_root = tmp_path / "Library" / "Caches"
    policy = replace(
        Policy.for_platform("macos"), safe_roots=(str(cache_root),)
    )

    evidence = classify(make_record(cache_root / "app" / "activity.log"), policy)

    assert {item.rule for item in evidence} == {
        "known-cache-root",
        "log-extension",
    }
    assert all(item.risk is Risk.LOW for item in evidence)
    assert all(item.actionable is True for item in evidence)


def test_cache_named_directory_outside_explicit_safe_roots_is_not_low_risk(tmp_path):
    policy = replace(Policy.for_platform("macos"), safe_roots=())

    evidence = classify(make_record(tmp_path / "Caches" / "opaque.bin"), policy)

    assert len(evidence) == 1
    assert evidence[0].rule == "unclassified"
    assert evidence[0].risk is Risk.REPORT_ONLY
    assert evidence[0].actionable is False


def test_os_managed_path_remains_report_only_even_when_it_looks_like_a_log():
    record = make_record(Path("/private/var/log/install.log"))

    evidence = classify(record, Policy.for_platform("macos"))

    assert len(evidence) == 1
    assert evidence[0].category == "system-managed"
    assert evidence[0].risk is Risk.REPORT_ONLY
    assert evidence[0].actionable is False


def test_unknown_file_is_report_only_instead_of_becoming_actionable(tmp_path):
    evidence = classify(
        make_record(tmp_path / "opaque.data"), Policy.for_platform("macos")
    )

    assert evidence[0].rule == "unclassified"
    assert evidence[0].actionable is False


def test_report_only_match_cannot_be_downgraded_by_download_location(tmp_path):
    downloads = tmp_path / "Downloads"
    policy = replace(
        Policy.for_platform("macos"), safe_roots=(str(downloads),)
    )

    evidence = classify(make_record(downloads / "active.sqlite"), policy)

    assert {item.rule for item in evidence} == {
        "database-extension",
        "download-location",
    }
    assert all(item.risk is Risk.REPORT_ONLY for item in evidence)
    assert all(item.actionable is False for item in evidence)
