"""Optional, throttled progress channel for the widget's scan subprocess."""
import json
import os
import sys
from dataclasses import asdict
from time import monotonic, time

_last = 0.0
_totals = dict(files_scanned=0, directories_scanned=0, bytes_scanned=0, errors=0)


def publish(update):
    global _last
    if os.environ.get("FREEUP_WIDGET_PROGRESS") != "1":
        return
    now = monotonic()
    if update.complete or now - _last >= 0.5:
        payload = asdict(update)
        payload["current_path"] = str(update.current_path or "")
        for key, value in _totals.items():
            payload[key] += value
        print("FREEUP_PROGRESS " + json.dumps(payload), file=sys.stderr, flush=True)
        _last = now
    if update.complete:
        for key in _totals:
            _totals[key] += getattr(update, key)


class StageProgress:
    """Progress for actual units of work; elapsed time alone is not activity."""

    def __init__(self, phase, total=None, unit="files"):
        self.phase, self.total, self.unit = phase, total, unit
        self.processed = 0
        self.started = time()
        self.last = 0.0
        self.enabled = os.environ.get("FREEUP_WIDGET_PROGRESS") == "1"
        self.update(0, force=True)

    def update(self, processed, force=False):
        self.processed = processed
        if not self.enabled:
            return
        now = monotonic()
        if force or now - self.last >= 0.5:
            print("FREEUP_STAGE " + json.dumps(dict(
                phase=self.phase, processed=processed, total=self.total, unit=self.unit,
                phase_started_at=self.started, updated_at=time())), file=sys.stderr, flush=True)
            self.last = now

    def advance(self, count):
        self.update(self.processed + count)

    def finish(self):
        self.update(self.processed, force=True)
