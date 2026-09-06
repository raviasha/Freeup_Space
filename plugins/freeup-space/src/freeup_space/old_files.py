"""Modification-age evidence for sufficiently large regular files."""

from __future__ import annotations

import calendar
import ntpath
from datetime import datetime
from typing import Iterable, List, Optional

from .categories import classify, reconcile_evidence
from .models import Evidence, FileRecord
from .policy import Policy


MIN_OLD_FILE_SIZE = 10_000


def _months_before(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _default_policy(record: FileRecord) -> Policy:
    path = str(record.path).replace("/", "\\")
    drive, tail = ntpath.splitdrive(path)
    platform = "windows" if (drive and tail.startswith("\\")) else "macos"
    return Policy.for_platform(platform)


def find_old_files(
    files: Iterable[FileRecord],
    now: datetime,
    months: int = 12,
    *,
    policy: Optional[Policy] = None,
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
        record_policy = policy or _default_policy(record)
        category_rule = record_policy.category_rules["old-file"]
        provisional = Evidence(
            path=record.path,
            category="old-file",
            rule="old-file-{}-months".format(months),
            reason="not modified for at least {} months".format(months),
            risk=category_rule.risk,
            actionable=category_rule.actionable,
            size=record.size,
        )
        context = [
            item
            for item in classify(record, record_policy)
            if item.rule != "unclassified"
        ]
        reconciled = reconcile_evidence(
            [provisional] + context, record_policy
        )[0]
        risk = reconciled.risk
        actionable = reconciled.actionable
        findings.append(
            Evidence(
                path=record.path,
                category="old-file",
                rule="old-file-{}-months".format(months),
                reason="not modified for at least {} months".format(months),
                risk=risk,
                actionable=actionable,
                size=record.size,
                details={
                    "modified_at": modified,
                    "threshold_at": threshold,
                    "threshold_months": months,
                    "minimum_size": MIN_OLD_FILE_SIZE,
                    "rule_risk": category_rule.risk.value,
                },
            )
        )
    findings.sort(key=lambda item: (-item.size, str(item.path)))
    return findings
