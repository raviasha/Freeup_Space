"""Private recovery records. Scan artifacts remain in their original folders."""
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path


def application_root():
    base = (Path.home() / "Library/Application Support" if sys.platform == "darwin"
            else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")))
    return base / "Freeup Space"


def workspace_key(workspace):
    return hashlib.sha256(str(Path(workspace).resolve()).encode("utf-8")).hexdigest()[:24]


def valid_run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value):
        raise ValueError("Invalid saved scan ID")
    return value


class WidgetStore:
    # Preview tokens and approval/request state are deliberately never serialized.
    FIELDS = ("session_id", "key", "workspace", "run_id", "mode", "status", "started_at",
              "closed", "review_state", "receipt")

    def __init__(self):
        self.root = Path(os.environ.get("FREEUP_WIDGET_STATE_DIR", application_root() / "widget-state"))

    def _read(self, name, large_record=False):
        path = self.root / name
        if not path.exists():
            return None
        if path.is_symlink() or not large_record and path.stat().st_size > 2_000_000:
            raise ValueError("Invalid widget recovery record")
        try:
            with path.open(encoding="utf-8") as stream:
                data = json.load(stream)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Saved widget state is unreadable; reopen a saved scan.") from error
        if not isinstance(data, dict):
            raise ValueError("Invalid widget recovery record")
        return data

    def load(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Invalid widget session")
        # Exact selections and receipts can legitimately contain every plan item.
        data = self._read(identifier + ".json", large_record=True)
        if data and (data.get("session_id") != identifier or not isinstance(data.get("key"), str)
                     or not Path(data.get("workspace", "")).is_absolute()):
            raise ValueError("Invalid widget recovery record")
        return data

    def latest(self, workspace):
        item = self._read("workspace-" + workspace_key(workspace) + ".json")
        return item.get("session_id") if item else None

    def review(self, workspace, run_id):
        name = "review-{}-{}.json".format(workspace_key(workspace), valid_run_id(run_id))
        return self._read(name, large_record=True) or {}

    def _write(self, name, value):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".widget-", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.root / name)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, session):
        data = {key: session.get(key) for key in self.FIELDS}
        self._write(session["session_id"] + ".json", data)
        self._write("workspace-" + workspace_key(session["workspace"]) + ".json",
                    {"session_id": session["session_id"]})
        if session.get("run_id") and (session.get("review_state") or {}).get("plan_digest"):
            name = "review-{}-{}.json".format(workspace_key(session["workspace"]), valid_run_id(session["run_id"]))
            self._write(name, {"review_state": session["review_state"],
                               "status": session["status"], "receipt": session.get("receipt")})

    def run_roots(self, workspace):
        # Support workspace-based artifacts and installed versions that kept them
        # outside iCloud in Application Support/Freeup Space/widget-workspaces.
        local = self.root.parent / "widget-workspaces" / workspace_key(workspace)
        return [Path(workspace) / ".freeup-space/runs", local / ".freeup-space/runs"]
