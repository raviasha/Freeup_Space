"""Modification-age evidence for sufficiently large regular files."""

from __future__ import annotations

import calendar
from datetime import datetime
from typing import Iterable, List

from .models import Evidence, FileRecord, Risk


MIN_OLD_FILE_SIZE = 10_000


def _months_before(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def find_old_files(
    files: Iterable[FileRecord], now: datetime, months: int = 12
) -> List[Evidence]:
    """Return old-file evidence using mtime and an inclusive calendar boundary."""

    if months <= 0:
        raise ValueError("months must be positive")
    threshold = _months_before(now, months)
    records = {}
    for record in sorted(files, key=lambda item: str(item.path)):
        records.setdefault((record.st_dev, record.st_ino), record)

    findings = []
    for record in records.values():
        if record.size < MIN_OLD_FILE_SIZE:
            continue
        if now.tzinfo is None:
            modified = datetime.fromtimestamp(record.mtime)
        else:
            modified = datetime.fromtimestamp(record.mtime, tz=now.tzinfo)
        if modified > threshold:
            continue
        findings.append(
            Evidence(
                path=record.path,
                category="old-file",
                rule="old-file-{}-months".format(months),
                reason="not modified for at least {} months".format(months),
                risk=Risk.MEDIUM,
                actionable=True,
                size=record.size,
                details={
                    "modified_at": modified,
                    "threshold_at": threshold,
                    "threshold_months": months,
                    "minimum_size": MIN_OLD_FILE_SIZE,
                },
            )
        )
    findings.sort(key=lambda item: (-item.size, str(item.path)))
    return findings
