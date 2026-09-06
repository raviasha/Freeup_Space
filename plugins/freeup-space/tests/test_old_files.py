import calendar
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from freeup_space.categories import classify
from freeup_space.models import FileRecord, Risk
from freeup_space.old_files import find_old_files
from freeup_space.policy import Policy


FIXED_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def months_before(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def make_file_with_mtime(path: Path, *, months_ago: int, size: int) -> FileRecord:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    modified = months_before(FIXED_NOW, months_ago)
    timestamp = modified.timestamp()
    os.utime(path, (timestamp, timestamp))
    source = path.stat()
    return FileRecord(
        path=path,
        size=source.st_size,
        allocated_size=getattr(source, "st_blocks", 0) * 512,
        mtime=source.st_mtime,
        atime=source.st_atime,
        ctime=source.st_ctime,
        st_dev=source.st_dev,
        st_ino=source.st_ino,
        volume_id=str(source.st_dev),
        file_id="{}:{}".format(source.st_dev, source.st_ino),
    )


def test_old_file_uses_twelve_month_default(tmp_path):
    record = make_file_with_mtime(
        tmp_path / "archive.zip", months_ago=13, size=10_000
    )

    evidence = find_old_files([record], FIXED_NOW)

    assert evidence[0].rule == "old-file-12-months"
    assert evidence[0].risk is Risk.MEDIUM
    assert evidence[0].details["threshold_months"] == 12


def test_file_younger_than_threshold_is_not_old(tmp_path):
    record = make_file_with_mtime(tmp_path / "recent.zip", months_ago=11, size=10_000)

    assert find_old_files([record], FIXED_NOW) == []


def test_old_file_below_minimum_size_is_not_a_candidate(tmp_path):
    record = make_file_with_mtime(tmp_path / "tiny.txt", months_ago=13, size=9_999)

    assert find_old_files([record], FIXED_NOW) == []


def test_exact_calendar_month_boundary_is_included(tmp_path):
    record = make_file_with_mtime(
        tmp_path / "boundary.zip", months_ago=12, size=10_000
    )

    evidence = find_old_files([record], FIXED_NOW)

    assert [item.path for item in evidence] == [record.path]


def test_overlap_evidence_keeps_one_logical_reclaimable_total(tmp_path):
    record = make_file_with_mtime(
        tmp_path / "Downloads" / "old.zip", months_ago=13, size=10_000
    )
    policy = replace(
        Policy.for_platform("macos"),
        safe_roots=(str(tmp_path / "Downloads"),),
    )

    evidence = classify(record, policy) + find_old_files(
        [record, record], FIXED_NOW
    )

    assert {item.rule for item in evidence} == {
        "archive-extension",
        "download-location",
        "old-file-12-months",
    }
    assert sum({item.path: item.size for item in evidence}.values()) == 10_000
