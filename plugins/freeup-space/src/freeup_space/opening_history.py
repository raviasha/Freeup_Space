"""Recorded application-opening metadata, kept separate from filesystem times."""
from __future__ import annotations

import math

from .models import FileRecord

SOURCE = "macos-launchservices"


def opening_evidence(record: FileRecord, now: float) -> dict:
    """Only a trustworthy, finite, past recorded opening counts as evidence."""
    value = record.last_opened_at
    if (record.last_opened_source == SOURCE and isinstance(value, (float, int))
            and not isinstance(value, bool) and 0 < value <= now and math.isfinite(value)):
        return {"last_opened_at": float(value), "source": SOURCE}
    return {"last_opened_at": None, "source": None}


def enrich_opening_history(run, *, max_files=10_000, max_seconds=5.0):
    """Bound opening metadata work once, before the Deep inventory checkpoint.

    The child can be terminated even if a native metadata service stalls. Only
    completed records are kept; every uncovered file remains explicitly unknown.
    """
    from dataclasses import replace
    from time import time
    from .categories import classify, _PERSONAL_EXTENSIONS
    from .policy import Policy
    from .widget_progress import StageProgress

    policy = Policy.for_platform(run.platform)
    stage = StageProgress("Selecting opening history", len(run.files))
    eligible = []
    for index, record in enumerate(run.files, 1):
        if record.file_kind == "regular" and record.path.suffix.casefold() in _PERSONAL_EXTENSIONS:
            findings = classify(record, policy)
            if (any(item.category == "personal-data" for item in findings)
                    and all(item.actionable for item in findings)):
                eligible.append(record)
        stage.update(index)
    stage.finish()
    selected = sorted(eligible, key=lambda item: str(item.path))[:max(0, max_files)]
    stage = StageProgress("Reading opening history", len(selected))
    metadata = {}
    status = "unavailable"
    if run.platform == "macos":
        if not selected:
            status = "complete" if not eligible else "budget-exhausted"
        elif max_seconds <= 0:
            status = "budget-exhausted"
        else:
            metadata, status = _read_metadata(tuple(selected), max_seconds)
    now = time()
    enriched = {}
    checked = 0
    known = 0
    for record in selected:
        key = str(record.path)
        if key not in metadata:
            continue
        checked += 1
        candidate = replace(record, last_opened_at=metadata[key], last_opened_source=SOURCE)
        evidence = opening_evidence(candidate, now)
        if evidence["last_opened_at"] is not None:
            known += 1
            enriched[key] = replace(candidate, last_opened_at=evidence["last_opened_at"])
        stage.update(checked)
    stage.finish()
    limited = len(selected) < len(eligible) or status == "budget-exhausted"
    coverage = {
        "status": "budget-exhausted" if limited and status == "complete" else status,
        "source": SOURCE if run.platform == "macos" else None,
        "eligible_files": len(eligible), "selected_files": len(selected),
        "checked_files": checked, "known_files": known,
        "unknown_files": len(eligible) - known,
        "file_budget": max_files, "time_budget_seconds": max_seconds,
        "budget_exhausted": limited,
    }
    return replace(run, files=tuple(enriched.get(str(item.path), item) for item in run.files),
                   coverage=dict(run.coverage, opening_history=coverage))


def _read_metadata(records, max_seconds):
    """One bounded child process for the whole batch; no shell or content reads."""
    import json
    import subprocess
    from . import runtime

    options = runtime.child_options()
    options["stdin"] = subprocess.PIPE
    try:
        child = subprocess.Popen(runtime.cli_command(["_opening-history-worker"]), **options)
    except OSError:
        return {}, "unavailable"
    try:
        requests = [{"path": str(item.path), "st_dev": item.st_dev, "st_ino": item.st_ino,
                     "size": item.size, "mtime": item.mtime} for item in records]
        output, _ = child.communicate(json.dumps(requests), timeout=max_seconds)
        status = "complete" if child.returncode == 0 else "unavailable"
    except subprocess.TimeoutExpired:
        child.kill()
        output, _ = child.communicate()
        status = "budget-exhausted"
    except BaseException:
        child.kill()
        child.communicate()
        raise
    allowed = {str(item.path) for item in records}
    result = {}
    for line in output.splitlines():
        try:
            path, value = json.loads(line)
            if isinstance(path, str) and path in allowed:
                result[path] = value
        except (TypeError, ValueError):
            continue
    return result, status


class _MacOSLastOpened:
    """CoreServices kMDItemLastUsedDate (LaunchServices-recorded last opening)."""

    def __init__(self):
        import ctypes
        from ctypes import c_void_p, c_char_p, c_long, c_bool, c_double, c_uint32

        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.md = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
        signatures = (
            (self.cf.CFStringCreateWithCString, [c_void_p, c_char_p, c_uint32], c_void_p),
            (self.cf.CFURLCreateFromFileSystemRepresentation, [c_void_p, c_char_p, c_long, c_bool], c_void_p),
            (self.cf.CFRelease, [c_void_p], None),
            (self.cf.CFGetTypeID, [c_void_p], ctypes.c_ulong),
            (self.cf.CFDateGetTypeID, [], ctypes.c_ulong),
            (self.cf.CFDateGetAbsoluteTime, [c_void_p], c_double),
            (self.md.MDItemCreateWithURL, [c_void_p, c_void_p], c_void_p),
            (self.md.MDItemCopyAttribute, [c_void_p, c_void_p], c_void_p),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result
        self.key = self.cf.CFStringCreateWithCString(None, b"kMDItemLastUsedDate", 0x08000100)
        if not self.key:
            raise OSError("last-opening attribute unavailable")

    def read(self, path):
        import os
        value = item = url = None
        try:
            encoded = os.fsencode(path)
            url = self.cf.CFURLCreateFromFileSystemRepresentation(None, encoded, len(encoded), False)
            if not url:
                return None
            item = self.md.MDItemCreateWithURL(None, url)
            if not item:
                return None
            value = self.md.MDItemCopyAttribute(item, self.key)
            if not value or self.cf.CFGetTypeID(value) != self.cf.CFDateGetTypeID():
                return None
            # CF absolute time starts on 2001-01-01; Unix time starts in 1970.
            return self.cf.CFDateGetAbsoluteTime(value) + 978307200.0
        finally:
            for reference in (value, item, url):
                if reference:
                    self.cf.CFRelease(reference)


def metadata_worker(args=None):
    """Private CLI entrypoint; stream results so a timeout preserves coverage."""
    import json
    import sys
    from pathlib import Path

    if sys.platform != "darwin":
        return 1
    import stat
    requests = json.load(sys.stdin)
    if (not isinstance(requests, list)
            or any(not isinstance(item, dict) or not isinstance(item.get("path"), str)
                   for item in requests)):
        raise ValueError("metadata worker requires file snapshots")
    native = _MacOSLastOpened()
    for item in requests:
        path = Path(item["path"])
        value = None
        try:
            expected = tuple(item[name] for name in ("st_dev", "st_ino", "size", "mtime"))

            def unchanged():
                current = path.lstat()
                return stat.S_ISREG(current.st_mode) and (
                    current.st_dev, current.st_ino, current.st_size, current.st_mtime
                ) == expected

            if unchanged():
                value = native.read(path)
                if not unchanged():
                    value = None
        except (OSError, ValueError, TypeError, KeyError):
            value = None
        print(json.dumps([str(path), value]), flush=True)
    return 0
