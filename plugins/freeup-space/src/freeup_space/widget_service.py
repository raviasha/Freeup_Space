"""Stateful widget workflow over the existing CLI. No arbitrary deletion API."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path

from .investigation import validate_notes
from .runtime import child_options, cli_command, plugin_root

ROOT = plugin_root()
BUSY = {"scanning", "previewing", "moving"}


class WidgetService:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.RLock()

    def open(self, workspace):
        directory = Path(workspace)
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("Workspace must be an existing absolute directory")
        with self.lock:
            identifier = secrets.token_hex(16)
            session = dict(session_id=identifier, key=secrets.token_urlsafe(32),
                           workspace=str(directory.resolve()), status="choose-mode", mode=None,
                           run_id=None, progress={}, started_at=None, preview=None,
                           receipt=None, error=None, process=None, requests=set(), plan=None,
                           ai_status=None, ai_notes=None, ai_requested=False)
            self.sessions[identifier] = session
            return self.view(session)

    def session(self, session_id, key=None):
        session = self.sessions.get(session_id)
        if session is None:
            raise ValueError("Widget session expired. Open Freeup Space again.")
        if key is not None and not secrets.compare_digest(session["key"], key):
            raise ValueError("Invalid widget session key")
        return session

    def view(self, session, offset=0, query="", category=""):
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("Invalid page offset")
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError("Invalid search")
        plan = session["plan"] or {}
        candidates = plan.get("candidates", [])
        actionable = [c for c in candidates if self.actionable(c)]
        visible = [c for c in candidates if (not category or c["category"] == category) and query.casefold() in
                   (c["path"] + " " + c["category"] + " " + c["candidate_id"]).casefold()]
        groups = {}
        for c in candidates:
            group = groups.setdefault(c["category"], dict(category=c["category"], count=0, actionable_count=0, bytes=0))
            group["count"] += 1
            if self.actionable(c):
                group["actionable_count"] += 1
                group["bytes"] += c["reclaimable_bytes"]
        result = {k: session[k] for k in ("session_id", "key", "status", "mode", "run_id",
                  "progress", "started_at", "preview", "receipt", "error", "ai_status", "ai_notes")}
        result.update(plan_digest=plan.get("digest"), candidates=[self.candidate(c) for c in visible[offset:offset+50]],
                      offset=offset, query=query, category=category, categories=sorted(groups.values(), key=lambda g: (-g["bytes"], g["category"])),
                      filtered_count=len(visible), candidate_count=len(candidates),
                      actionable_count=len(actionable), reclaimable_bytes=sum(c["reclaimable_bytes"] for c in actionable),
                      coverage=session.get("coverage", {}), ai_finding_count=session.get("ai_finding_count", 0))
        return result

    @staticmethod
    def actionable(candidate):
        return (candidate.get("actionable") is True and candidate.get("risk") != "report-only"
                and candidate.get("proposed_action") == "trash")

    def candidate(self, candidate):
        return {**{k: candidate.get(k) for k in
                   ("candidate_id", "path", "category", "risk", "reclaimable_bytes", "reasons")},
                "actionable": self.actionable(candidate),
                "retained_path": candidate.get("evidence", {}).get("retained_path")}

    def directory(self, session):
        return Path(session["workspace"]) / ".freeup-space" / "runs" / session["run_id"]

    def load(self, session, filename):
        return json.loads((self.directory(session) / filename).read_text(encoding="utf-8"))

    def command(self, session, args, progress=False):
        options = child_options()
        options["env"]["FREEUP_WIDGET_PROGRESS"] = "1" if progress else "0"
        process = subprocess.Popen(cli_command(args), cwd=session["workspace"], **options)
        with self.lock:
            session["process"] = process
            if session["status"] == "cancelled":
                process.terminate()
        messages = []

        def stderr_reader():
            for line in process.stderr:
                if line.startswith("FREEUP_PROGRESS "):
                    try:
                        update = json.loads(line[len("FREEUP_PROGRESS "):])
                        with self.lock:
                            session["progress"] = dict(update, phase="Inventory")
                    except ValueError:
                        pass
                elif line.startswith(("Inventory:", "Analysis:")):
                    with self.lock:
                        session["progress"] = dict(session["progress"], message=line.strip(),
                                                   phase="Duplicate analysis" if line.startswith("Analysis:") else "Inventory")
                else:
                    messages.append(line)
                    del messages[:-30]
            process.stderr.close()

        reader = threading.Thread(target=stderr_reader, daemon=True)
        reader.start()
        output = process.stdout.read()
        process.stdout.close()
        process.wait()
        reader.join()
        with self.lock:
            session["process"] = None
        if process.returncode:
            raise ValueError(("".join(messages).strip() or "Operation interrupted")[-3000:])
        return json.loads(output)

    def background(self, session, fn):
        def work():
            try:
                fn()
            except Exception as error:
                with self.lock:
                    if session["status"] != "cancelled":
                        session.update(status="error", error=str(error), preview=None)
        threading.Thread(target=work, daemon=True).start()

    def start(self, session, mode, request_id, paths=None):
        if mode not in {"quick", "deep", "deep-ai"}:
            raise ValueError("Choose Quick Scan, Deep Scan, or Deep Scan + AI")
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100:
            raise ValueError("Invalid scan request")
        paths = paths or []
        if not isinstance(paths, list) or len(paths) > 20 or any(
                not isinstance(p, str) or not Path(p).is_absolute() or not Path(p).exists() for p in paths):
            raise ValueError("Scan roots must be existing absolute paths")
        with self.lock:
            if request_id in session["requests"]:
                return self.view(session)
            if session["status"] in BUSY or session["process"] is not None:
                raise ValueError("Wait for the current operation to finish")
            session["requests"].add(request_id)
            session.update(status="scanning", mode=mode, run_id="widget-" + secrets.token_hex(12),
                           progress={"phase": "Starting scan"}, started_at=time.time(), preview=None,
                           receipt=None, plan=None, error=None, ai_status=None, ai_notes=None,
                           ai_requested=False, coverage={}, ai_finding_count=0)

            def scan():
                summary = self.command(session, ["start", "--mode", mode, "--run-id", session["run_id"],
                                                "--summary-only", "--", *paths], progress=True)
                run = self.load(session, "run.json")
                plan = self.load(session, "plan.json")
                if not run.get("complete") or run.get("coverage", {}).get("analysis_pending"):
                    raise ValueError("Scan analysis has not completed")
                packet = self.load(session, "investigation.json") if mode == "deep-ai" else None
                with self.lock:
                    if session["status"] == "cancelled":
                        return
                    session.update(status="review", plan=plan, coverage=summary.get("coverage", {}),
                                   ai_status=packet["status"] if packet else None,
                                   ai_finding_count=len(packet["findings"]) if packet else 0)
            self.background(session, scan)
            return self.view(session)

    def cancel(self, session):
        with self.lock:
            if session["status"] != "scanning":
                raise ValueError("Only a scan can be cancelled")
            session["status"] = "cancelled"
            if session["process"] is not None:
                session["process"].terminate()
            return self.view(session)

    def check_plan(self, session):
        run = self.load(session, "run.json")
        plan = self.load(session, "plan.json")
        if (not run.get("complete") or run.get("coverage", {}).get("analysis_pending")
                or plan.get("action") != "trash" or plan.get("digest") != session["plan"]["digest"]):
            raise ValueError("The plan changed. Start a new scan and review it.")
        return plan

    def preview(self, session, ids, plan_digest):
        with self.lock:
            if session["status"] not in {"review", "preview"}:
                raise ValueError("Complete the scan before previewing")
            plan = self.check_plan(session)
            if plan_digest != plan["digest"]:
                raise ValueError("The displayed plan is stale")
            if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(i, str) for i in ids):
                raise ValueError("Select between 1 and 500 candidates")
            allowed = {c["candidate_id"]: c for c in plan["candidates"] if self.actionable(c)}
            if len(set(ids)) != len(ids) or any(i not in allowed for i in ids):
                raise ValueError("Selection contains unknown, duplicate, or report-only candidate IDs")
            selected = [self.candidate(allowed[i]) for i in ids]
            session.update(status="previewing", preview=None, error=None)

            def prepare():
                receipt = self.command(session, ["apply", "--run-id", session["run_id"], "--dry-run", *ids])
                blocked = [s for s in receipt["skipped"] if s["reason"] != "dry-run"] + receipt["failed"]
                with self.lock:
                    session.update(status="preview", preview=dict(token=secrets.token_urlsafe(32),
                                   expires_at=time.time()+600, plan_digest=plan_digest,
                                   ids=list(ids), candidates=selected, blocked=blocked,
                                   reclaimable_bytes=sum(c["reclaimable_bytes"] for c in selected)))
            self.background(session, prepare)
            return self.view(session)

    def confirm(self, session, token):
        with self.lock:
            preview = session.get("preview")
            if session["status"] in {"moving", "done"} and preview and secrets.compare_digest(preview["token"], token):
                return self.view(session)  # Retrying an acknowledged confirmation never moves twice.
            if session["status"] != "preview" or not preview or not secrets.compare_digest(preview["token"], token):
                raise ValueError("Preview the selection before confirming")
            if time.time() > preview["expires_at"] or preview["blocked"]:
                raise ValueError("Preview expired or contains changed files. Review the selection again.")
            self.check_plan(session)
            session["status"] = "moving"

            def move():
                receipt = self.command(session, ["apply", "--run-id", session["run_id"], *preview["ids"]])
                with self.lock:
                    session.update(status="done", receipt=receipt)
            self.background(session, move)
            return self.view(session)

    def investigation(self, session):
        with self.lock:
            if session["mode"] != "deep-ai" or not session["plan"]:
                raise ValueError("AI investigation requires a completed Deep Scan + AI")
            self.check_plan(session)
            session["ai_status"] = "investigating"
            return self.load(session, "investigation.json")

    def record_notes(self, session, notes):
        with self.lock:
            if session["mode"] != "deep-ai" or not session["plan"]:
                raise ValueError("AI investigation requires a completed Deep Scan + AI")
            self.check_plan(session)
            packet = self.load(session, "investigation.json")
            validated = validate_notes(packet, notes)
            directories = {f["finding_id"]: f["directory"] for f in packet["findings"]}
            for finding in validated["findings"]:
                finding["directory"] = directories[finding["finding_id"]]
            remaining = len(packet["findings"]) - len(validated["findings"])
            validated["uninvestigated_count"] = remaining
            destination = self.directory(session) / "ai-notes.json"
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(validated, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, destination)
            session.update(ai_status="partial" if remaining else "complete", ai_notes=validated)
            return {"session_id": session["session_id"], "status": session["ai_status"],
                    "finding_count": len(validated["findings"]), "uninvestigated_count": remaining}

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                if session["status"] == "scanning" and session["process"]:
                    session["process"].terminate()
