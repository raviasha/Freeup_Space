"""Optional, throttled progress channel for the widget's scan subprocess."""
import json
import os
import sys
from dataclasses import asdict
from time import monotonic

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
