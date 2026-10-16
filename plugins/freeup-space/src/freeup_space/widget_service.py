"""Stateful widget workflow over the existing CLI. No arbitrary deletion API."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path

from .investigation import validate_notes
from .runtime import child_options, cli_command, plugin_root
from .json_stream import artifact_path, load_json
from .similar_groups import build_groups
from .recommendations import build_recommendations
from .widget_store import WidgetStore, valid_run_id, workspace_key

ROOT = plugin_root()
BUSY = {"scanning", "loading", "previewing", "moving"}


class WidgetService:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.RLock()
        self.store = WidgetStore()

    def open(self, workspace, run_id=None):
        directory = Path(workspace)
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("Workspace must be an existing absolute directory")
        with self.lock:
            workspace = str(directory.resolve())
            if run_id is not None:
                self.saved_directory(workspace, run_id)
            try:
                identifier = self.store.latest(workspace)
            except (ValueError, OSError):
                identifier = None  # Saved scan artifacts can outlive recovery metadata.
            if identifier:
                try:
                    session = self.session(identifier)
                except (ValueError, OSError):
                    session = None
                if session is not None and session["workspace"] == workspace:
                    session["closed"] = False
                    if run_id is not None and run_id != session.get("run_id"):
                        return self.open_saved(session, run_id)
                    self.store.save(session)
                    return self.view(session)
            identifier = secrets.token_hex(16)
            session = dict(session_id=identifier, key=secrets.token_urlsafe(32),
                           workspace=str(directory.resolve()), status="choose-mode", mode=None,
                           run_id=None, progress={}, started_at=None, preview=None,
                           receipt=None, error=None, process=None, requests=set(), plan=None,
                           ai_status=None, ai_notes=None, ai_requested=False,
                           closed=False, review_state={}, selected_candidates=[], saved_scans=[])
            self.sessions[identifier] = session
            session["saved_scans"] = self.saved_scans(workspace)
            chosen = run_id or (session["saved_scans"][0]["run_id"] if session["saved_scans"] else None)
            if chosen:
                return self.open_saved(session, chosen)
            self.store.save(session)
            return self.view(session)

    def session(self, session_id, key=None):
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                saved = self.store.load(session_id)
                if saved is None:
                    raise ValueError("Widget session expired. Open Freeup Space again.")
                if key is not None and not secrets.compare_digest(saved["key"], key):
                    raise ValueError("Invalid widget session key")
                session = dict(saved, process=None, requests=set(), plan=None, view_index=None,
                               progress={}, preview=None, error=None, ai_status=None, ai_notes=None,
                               ai_requested=False, selected_candidates=[], saved_scans=[])
                self.sessions[session_id] = session
                session["saved_scans"] = self.saved_scans(session["workspace"])
                if saved.get("run_id"):
                    try:
                        self._restore(session, saved["run_id"], saved)
                    except (ValueError, OSError) as error:
                        session.update(status="error", error=str(error))
                else:
                    session["status"] = "choose-mode"
            if key is not None and not secrets.compare_digest(session["key"], key):
                raise ValueError("Invalid widget session key")
            return session

    def saved_directory(self, workspace, run_id):
        valid_run_id(run_id)
        for root in self.store.run_roots(workspace):
            directory = root / run_id
            if directory.is_dir() and not directory.is_symlink():
                return directory
        raise ValueError("Saved scan is unavailable in this workspace")

    def saved_scans(self, workspace):
        results = {}
        for root in self.store.run_roots(workspace):
            if not root.is_dir():
                continue
            for directory in root.iterdir():
                try:
                    valid_run_id(directory.name)
                    if not directory.is_dir() or directory.is_symlink():
                        continue
                    status_path = artifact_path(directory / "status.json")
                    # Do not deserialize a multi-gigabyte inventory for a menu.
                    if not status_path.exists():
                        continue
                    saved = load_json(status_path)
                    if (saved.get("complete") and not saved.get("coverage", {}).get("analysis_pending")
                            and artifact_path(directory / "plan.json").exists()):
                        results.setdefault(directory.name, {"run_id": directory.name,
                            "mode": saved.get("mode", "scan"), "saved_at": status_path.stat().st_mtime})
                except (ValueError, OSError):
                    continue
        return sorted(results.values(), key=lambda r: r["saved_at"], reverse=True)

    def _restore(self, session, run_id, saved=None):
        directory = self.saved_directory(session["workspace"], run_id)
        session.update(run_id=run_id, artifact_workspace=str(directory.parent.parent.parent),
                       status="loading", plan=None, view_index=None, preview=None, error=None,
                       progress={"phase": "Loading saved results"}, ai_auto_request=False, can_resume=False)
        previous = saved or self.store.review(session["workspace"], run_id)

        def load():
            status = self.run_status(session)
            with self.lock:
                session.update(mode=status.get("mode"), coverage=status.get("coverage", {}))
            if not status.get("complete") or status.get("coverage", {}).get("analysis_pending"):
                raise ValueError("This saved scan was interrupted before analysis finished. Its inventory is preserved.")
            plan = self.load(session, "plan.json")
            if plan.get("run_id") != run_id or plan.get("action") != "trash":
                raise ValueError("Saved cleanup plan does not match this scan")
            review = previous.get("review_state") or {}
            matches = review.get("plan_digest") == plan.get("digest")
            if not matches:
                review = {}
            receipt = previous.get("receipt") if matches else None
            wanted = set(review.get("ids", []))
            selected = [self.candidate(c) for c in plan["candidates"]
                        if c["candidate_id"] in wanted and self.actionable(c)]
            review = dict(ids=[c["candidate_id"] for c in selected], plan_digest=plan["digest"],
                          category=review.get("category", ""), query=review.get("query", ""), offset=review.get("offset", 0),
                          similar_group=review.get("similar_group", ""), group_offset=review.get("group_offset", 0))
            review["selection_digest"] = self.selection_digest(review["ids"], plan["digest"])
            ai = dict(ai_status=None, ai_notes=None, ai_finding_count=0, ai_requested=False)
            if status.get("mode") == "deep-ai":
                try:
                    packet = self.load(session, "investigation.json")
                    if packet.get("run_id") != run_id or packet.get("plan_digest") != plan["digest"]:
                        raise ValueError("Saved AI review does not match this scan")
                    ai.update(ai_status=packet["status"], ai_finding_count=len(packet["findings"]))
                    if artifact_path(directory / "ai-notes.json").exists():
                        notes = self.validated_notes(packet, self.load(session, "ai-notes.json"))
                        ai.update(ai_notes=notes, ai_status="partial" if notes["uninvestigated_count"] else "complete")
                except (ValueError, OSError, KeyError, TypeError):
                    ai["error"] = "Some saved AI findings could not be restored. The scan results are still available."
            with self.lock:
                session.update(plan=plan, mode=status.get("mode"), coverage=status.get("coverage", {}),
                    review_state=review, selected_candidates=selected, receipt=receipt,
                    status="done" if previous.get("status") == "done" and receipt else "review", **ai)
                if previous.get("status") == "moving":
                    session["error"] = "The previous cleanup was interrupted. Review the saved receipt and preview remaining files again."
                self.store.save(session)
        self.background(session, load)

    def open_saved(self, session, run_id):
        with self.lock:
            if session["status"] in BUSY:
                raise ValueError("Wait for the current operation to finish")
            self.saved_directory(session["workspace"], run_id)
            session.update(closed=False, review_state={}, selected_candidates=[], receipt=None,
                           ai_status=None, ai_notes=None, ai_requested=False)
            self._restore(session, run_id)
            self.store.save(session)
            return self.view(session)

    def save_review(self, session, ids, plan_digest, category="", query="", offset=0, similar_group="", group_offset=0):
        with self.lock:
            if session["status"] != "review" or not session["plan"] or session["closed"]:
                raise ValueError("Open the scan review before saving a selection")
            if plan_digest != session["plan"]["digest"]:
                raise ValueError("The displayed plan is stale")
            if (not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError("Choose distinct candidate IDs")
            if (not isinstance(query, str) or len(query) > 500 or not isinstance(category, str)
                    or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0):
                raise ValueError("Invalid review filters")
            self.view(session, offset, query, category, similar_group, group_offset)
            lookup = session["view_index"]["by_id"]
            candidates = [self.candidate(lookup[i]) for i in ids
                          if i in lookup and self.actionable(lookup[i])]
            if len(candidates) != len(ids):
                raise ValueError("Selection contains unknown or report-only items")
            session.update(review_state=dict(ids=list(ids), plan_digest=plan_digest, category=category, query=query,
                           offset=offset, similar_group=similar_group, group_offset=group_offset,
                           selection_digest=self.selection_digest(ids, plan_digest)),
                           selected_candidates=candidates)
            self.store.save(session)
            return self.view(session, offset, query, category, similar_group, group_offset)

    @staticmethod
    def selection_digest(ids, plan_digest):
        return hashlib.sha256(json.dumps([plan_digest, ids], separators=(",", ":")).encode("utf-8")).hexdigest()

    def update_review(self, session, add_ids, remove_ids, plan_digest, selection_digest=None, **filters):
        with self.lock:
            review = session.get("review_state") or {}
            if selection_digest is not None and selection_digest != review.get("selection_digest"):
                raise ValueError("The selection changed. Reconnect before editing it.")
            for ids in (add_ids, remove_ids):
                if (not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(i, str) for i in ids)
                        or len(set(ids)) != len(ids)):
                    raise ValueError("Use at most 500 distinct IDs per selection update")
            if set(add_ids) & set(remove_ids):
                raise ValueError("An item cannot be added and removed together")
            ids = dict.fromkeys(review.get("ids", []))
            for identifier in remove_ids:
                ids.pop(identifier, None)
            ids.update(dict.fromkeys(add_ids))
            return self.save_review(session, list(ids), plan_digest, **filters)

    def select_group(self, session, group_id, plan_digest, action):
        with self.lock:
            if session["status"] != "review" or session["closed"] or not session["plan"]:
                raise ValueError("Open the scan review before selecting a group")
            if plan_digest != session["plan"]["digest"]:
                raise ValueError("The displayed plan is stale")
            if action not in {"select", "clear"}:
                raise ValueError("Choose select or clear")
            self.view(session)
            groups = session["view_index"]["similar"]
            if group_id not in groups["members"]:
                raise ValueError("Unknown similar-name group")
            review = session.get("review_state") or {}
            ids = dict.fromkeys(review.get("ids", []))
            for c in groups["members"][group_id]:
                if action == "select" and self.actionable(c):
                    ids[c["candidate_id"]] = None
                elif action == "clear":
                    ids.pop(c["candidate_id"], None)
            return self.save_review(session, list(ids), plan_digest, review.get("category", ""),
                review.get("query", ""), review.get("offset", 0), review.get("similar_group", ""), review.get("group_offset", 0))

    def select_recommended(self, session, category, plan_digest, action):
        with self.lock:
            if session["status"] != "review" or session["closed"] or not session["plan"]:
                raise ValueError("Open the scan review before selecting recommendations")
            if plan_digest != session["plan"]["digest"]:
                raise ValueError("The displayed plan is stale")
            if action not in {"select", "clear"}:
                raise ValueError("Choose select or clear")
            self.view(session)
            index = session["view_index"]["recommendations"]
            if category not in index["recommended"]:
                raise ValueError("Unknown recommendation category")
            recommended = index["recommended"][category]
            review = session.get("review_state") or {}
            ids = dict.fromkeys(review.get("ids", []))
            if action == "select":
                keep = set().union(*(index["dependencies"].get(i, set()) for i in recommended))
                if keep.intersection(ids):
                    raise ValueError("A file marked Keep is already selected. Clear it before selecting these recommendations.")
                ids.update(dict.fromkeys(recommended))
            else:
                for identifier in recommended:
                    ids.pop(identifier, None)
            return self.save_review(session, list(ids), plan_digest, review.get("category", ""),
                review.get("query", ""), review.get("offset", 0), review.get("similar_group", ""), review.get("group_offset", 0))

    def dismiss(self, session):
        with self.lock:
            session["closed"] = True
            if session["status"] == "preview":
                session.update(status="review", preview=None)
            self.store.save(session)
            return self.view(session)

    def cancel_preview(self, session):
        with self.lock:
            if session["status"] != "preview":
                raise ValueError("No active preview")
            session.update(status="review", preview=None)
            self.store.save(session)
            return self.view(session)

    def view(self, session, offset=0, query="", category="", similar_group="", group_offset=0):
        if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in (offset, group_offset)):
            raise ValueError("Invalid page offset")
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError("Invalid search")
        if not isinstance(similar_group, str) or not isinstance(category, str):
            raise ValueError("Invalid review filters")
        plan = session["plan"] or {}
        candidates = plan.get("candidates", [])
        cached = session.get("view_index")
        if cached is None or cached["plan"] is not plan or cached["digest"] != plan.get("digest"):
            groups, by_category, by_id = {}, {}, {}
            count = total = 0
            for c in candidates:
                by_id[c["candidate_id"]] = c
                group = groups.setdefault(c["category"], dict(category=c["category"], count=0, actionable_count=0, bytes=0))
                by_category.setdefault(c["category"], []).append(c)
                group["count"] += 1
                if self.actionable(c):
                    group["actionable_count"] += 1
                    group["bytes"] += c["reclaimable_bytes"]
                    count += 1
                    total += c["reclaimable_bytes"]
            cached = dict(plan=plan, digest=plan.get("digest"), by_category=by_category, by_id=by_id,
                          groups=sorted(groups.values(), key=lambda g: (-g["bytes"], g["category"])),
                          actionable_count=count, reclaimable_bytes=total, filter_key=None,
                          similar=build_groups(candidates, self.actionable), group_query=None,
                          recommendations=build_recommendations(plan, self.actionable))
            session["view_index"] = cached
        similar = cached["similar"]
        recommendations = cached["recommendations"]
        key = (category, query.casefold(), similar_group)
        if cached["filter_key"] != key:
            source = (similar["members"].get(similar_group, []) if similar_group else
                      recommendations["members"][category] if category in recommendations["members"] else
                      cached["by_category"].get(category, []) if category else candidates)
            cached["visible"] = [c for c in source if key[1] in
                (c["path"] + " " + c["category"] + " " + c["candidate_id"]).casefold()] if query else source
            cached["filter_key"] = key
        if cached["group_query"] != key[1]:
            matching = {similar["by_candidate"].get(c["candidate_id"]) for c in candidates
                        if key[1] in c["path"].casefold()} if query else set(similar["groups"])
            cached["visible_groups"] = [g for g in similar["ordered"] if g["group_id"] in matching
                                        or query and key[1] in g["label"].casefold()]
            cached["visible_versions"] = [k for k in recommendations["version_cards"] if not query or
                any(key[1] in c["path"].casefold() for c in recommendations["members"][k])]
            cached["group_query"] = key[1]
        visible = cached["visible"]
        result = {k: session[k] for k in ("session_id", "key", "status", "mode", "run_id",
                  "progress", "started_at", "preview", "receipt", "error", "ai_status", "ai_notes")}
        version_keys = cached["visible_versions"]
        result.update(plan_digest=plan.get("digest"), candidates=[self.grouped_candidate(c, similar, recommendations["details"].get(c["candidate_id"])) for c in visible[offset:offset+50]],
                      offset=offset, query=query, category=category, categories=cached["groups"],
                      review_categories=recommendations["cards"],
                      active_recommendation=recommendations["descriptions"].get(category),
                      version_groups=[recommendations["descriptions"][k] for k in version_keys[group_offset:group_offset+20]],
                      version_group_count=len(version_keys), opening_history_unknown_count=recommendations["unknown_count"],
                      similar_group=similar_group, group_offset=group_offset,
                      active_group=similar["groups"].get(similar_group),
                      similar_groups=cached["visible_groups"][group_offset:group_offset+20],
                      similar_group_count=len(cached["visible_groups"]),
                      filtered_count=len(visible), candidate_count=len(candidates),
                      actionable_count=cached["actionable_count"], reclaimable_bytes=cached["reclaimable_bytes"],
                      coverage=session.get("coverage", {}), ai_finding_count=session.get("ai_finding_count", 0))
        result.update(can_resume=session.get("can_resume", False) and session["status"] in {"error", "cancelled"}, closed=session.get("closed", False), workspace=session["workspace"],
                      ai_auto_request=session.get("ai_auto_request", False),
                      review_state=session.get("review_state", {}), selected_candidates=session.get("selected_candidates", []),
                      saved_scans=session.get("saved_scans", []))
        return result

    def grouped_candidate(self, candidate, similar, review_details=None):
        result = self.candidate(candidate, review_details)
        group = similar["groups"].get(similar["by_candidate"].get(candidate["candidate_id"]))
        if group:
            result.update(similar_group_id=group["group_id"], similar_group_count=group["actionable_count"],
                          similar_group_label=group["label"])
        return result

    @staticmethod
    def actionable(candidate):
        return (candidate.get("actionable") is True and candidate.get("risk") != "report-only"
                and candidate.get("proposed_action") == "trash")

    def candidate(self, candidate, review_details=None):
        return {**{k: candidate.get(k) for k in
                   ("candidate_id", "path", "category", "risk", "reclaimable_bytes", "reasons")},
                "actionable": self.actionable(candidate),
                "review_details": review_details,
                "retained_path": candidate.get("evidence", {}).get("retained_path")}

    def directory(self, session):
        return Path(session.get("artifact_workspace", session["workspace"])) / ".freeup-space" / "runs" / session["run_id"]

    def load(self, session, filename):
        return load_json(self.directory(session) / filename)

    def run_status(self, session):
        filename = "status.json" if artifact_path(self.directory(session) / "status.json").exists() else "run.json"
        return self.load(session, filename)

    def command(self, session, args, progress=False):
        options = child_options()
        options["env"]["FREEUP_WIDGET_PROGRESS"] = "1" if progress else "0"
        input_ids = None
        # Keep large selections off the OS command line (especially on Windows).
        if args[0] == "apply":
            first_id = 4 if args[3:4] == ["--dry-run"] else 3
            if len(args) - first_id > 400:
                input_ids = json.dumps(args[first_id:])
                args = [*args[:first_id], "--ids-stdin"]
                options["stdin"] = subprocess.PIPE
        process = subprocess.Popen(cli_command(args), cwd=session.get("artifact_workspace", session["workspace"]), **options)
        with self.lock:
            session["process"] = process
            if session["status"] == "cancelled":
                process.terminate()
        messages = []

        def stderr_reader():
            for line in process.stderr:
                if line.startswith("FREEUP_STAGE "):
                    try:
                        update = json.loads(line[len("FREEUP_STAGE "):])
                        with self.lock:
                            session["progress"] = dict(session["progress"], **update)
                            session["progress"].pop("current_path", None)
                            session["progress"].pop("message", None)
                    except (ValueError, TypeError):
                        pass
                elif line.startswith("FREEUP_EVENT "):
                    try:
                        event = json.loads(line[len("FREEUP_EVENT "):])
                        if event.get("event") in {"inventory-saved", "partial-results"} and event.get("run_id") == session["run_id"]:
                            with self.lock:
                                session["can_resume"] = True
                    except (ValueError, TypeError):
                        pass
                elif line.startswith("FREEUP_PROGRESS "):
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
        if input_ids is not None:
            try:
                process.stdin.write(input_ids)
            except BrokenPipeError:
                pass  # The child's error is returned below.
            finally:
                process.stdin.close()
        output = process.stdout.read()
        process.stdout.close()
        process.wait()
        reader.join()
        with self.lock:
            session["process"] = None
        if process.returncode:
            raise ValueError(("".join(messages).strip() or "Operation interrupted")[-3000:])
        return json.loads(output)

    def update_resume(self, session):
        if session.get("mode") in {"deep", "deep-ai"} and session.get("run_id"):
            try:
                saved = self.run_status(session)
                session["can_resume"] = bool(saved.get("coverage", {}).get("inventory_finished")
                    and (not saved.get("complete") or saved.get("coverage", {}).get("analysis_pending")))
            except (OSError, ValueError):
                session["can_resume"] = False

    def background(self, session, fn):
        def work():
            try:
                fn()
            except Exception as error:
                with self.lock:
                    if session["status"] != "cancelled":
                        self.update_resume(session)
                        session.update(status="error", error=str(error), preview=None)
                        self.store.save(session)
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
            artifact_workspace = self.store.root.parent / "widget-workspaces" / workspace_key(session["workspace"])
            artifact_workspace.mkdir(parents=True, exist_ok=True)
            session["artifact_workspace"] = str(artifact_workspace)
            session.update(status="scanning", mode=mode, run_id="widget-" + secrets.token_hex(12),
                           progress={"phase": "Starting scan"}, started_at=time.time(), preview=None,
                           receipt=None, plan=None, view_index=None, error=None, ai_status=None, ai_notes=None,
                           ai_requested=False, coverage={}, ai_finding_count=0, closed=False,
                           review_state={}, selected_candidates=[], ai_auto_request=True, can_resume=False)
            self.store.save(session)

            def scan():
                summary = self.command(session, ["start", "--mode", mode, "--run-id", session["run_id"],
                                                "--summary-only", "--", *paths], progress=True)
                with self.lock:
                    session["progress"] = dict(session["progress"], phase="Loading results",
                        processed=0, total=None, unit="items", phase_started_at=time.time(), updated_at=time.time())
                run = self.run_status(session)
                plan = self.load(session, "plan.json")
                if not run.get("complete") or run.get("coverage", {}).get("analysis_pending"):
                    raise ValueError("Scan analysis has not completed")
                packet = self.load(session, "investigation.json") if mode == "deep-ai" else None
                with self.lock:
                    if session["status"] == "cancelled":
                        return
                    session.update(status="review", plan=plan, coverage=summary.get("coverage", {}), can_resume=False,
                                   ai_status=packet["status"] if packet else None,
                                   ai_finding_count=len(packet["findings"]) if packet else 0)
                    session["saved_scans"] = self.saved_scans(session["workspace"])
                    self.store.save(session)
            self.background(session, scan)
            return self.view(session)

    def resume(self, session, request_id):
        with self.lock:
            if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100:
                raise ValueError("Invalid resume request")
            if request_id in session["requests"]:
                return self.view(session)
            if session["status"] not in {"error", "cancelled"} or not session.get("can_resume") or session["process"]:
                raise ValueError("Only an interrupted scan with a saved inventory can resume")
            session["requests"].add(request_id)
            session.update(status="scanning", preview=None, plan=None, view_index=None, error=None,
                           started_at=time.time(), progress={"phase": "Resuming duplicate analysis"})

            self.store.save(session)

            def analyze():
                summary = self.command(session, ["start", "--mode", session["mode"],
                    "--resume", session["run_id"], "--summary-only"], progress=True)
                run = self.run_status(session)
                if not run.get("complete") or run.get("coverage", {}).get("analysis_pending"):
                    raise ValueError("Scan analysis has not completed")
                plan = self.load(session, "plan.json")
                packet = self.load(session, "investigation.json") if session["mode"] == "deep-ai" else None
                with self.lock:
                    if session["status"] != "cancelled":
                        session.update(status="review", plan=plan, can_resume=False,
                            coverage=summary.get("coverage", {}),
                            ai_status=packet["status"] if packet else None,
                            ai_finding_count=len(packet["findings"]) if packet else 0)
                        session["saved_scans"] = self.saved_scans(session["workspace"])
                        self.store.save(session)
            self.background(session, analyze)
            return self.view(session)

    def cancel(self, session):
        with self.lock:
            if session["status"] != "scanning":
                raise ValueError("Only a scan can be cancelled")
            session["status"] = "cancelled"
            self.update_resume(session)
            self.store.save(session)
            if session["process"] is not None:
                session["process"].terminate()
            return self.view(session)

    def check_plan(self, session):
        run = self.run_status(session)
        plan = self.load(session, "plan.json")
        if (not run.get("complete") or run.get("coverage", {}).get("analysis_pending")
                or plan.get("action") != "trash" or plan.get("digest") != session["plan"]["digest"]):
            raise ValueError("The plan changed. Start a new scan and review it.")
        return plan

    def preview(self, session, ids, plan_digest, selection_digest=None):
        with self.lock:
            if session["status"] not in {"review", "preview"}:
                raise ValueError("Complete the scan before previewing")
            if ids is None:
                review = session.get("review_state") or {}
                if not selection_digest or selection_digest != review.get("selection_digest"):
                    raise ValueError("The selection changed. Reconnect and review it again.")
                ids = list(review.get("ids", []))
            plan = self.check_plan(session)
            if plan_digest != plan["digest"]:
                raise ValueError("The displayed plan is stale")
            if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
                raise ValueError("Select at least one candidate")
            allowed = {c["candidate_id"]: c for c in plan["candidates"] if self.actionable(c)}
            if len(set(ids)) != len(ids) or any(i not in allowed for i in ids):
                raise ValueError("Selection contains unknown, duplicate, or report-only candidate IDs")
            self.view(session)
            review_details = session["view_index"]["recommendations"]["details"]
            selected = [self.candidate(allowed[i], review_details.get(i)) for i in ids]
            review = session.get("review_state") or {}
            session.update(review_state=dict(review, ids=list(ids), plan_digest=plan_digest,
                selection_digest=self.selection_digest(ids, plan_digest)), selected_candidates=selected)
            session.update(status="previewing", preview=None, error=None)
            self.store.save(session)

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
            self.store.save(session)

            def move():
                receipt = self.command(session, ["apply", "--run-id", session["run_id"], *preview["ids"]])
                with self.lock:
                    session.update(status="done", receipt=receipt)
                    self.store.save(session)
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
            validated = self.validated_notes(packet, notes)
            remaining = validated["uninvestigated_count"]
            destination = self.directory(session) / "ai-notes.json"
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(validated, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, destination)
            session.update(ai_status="partial" if remaining else "complete", ai_notes=validated)
            return {"session_id": session["session_id"], "status": session["ai_status"],
                    "finding_count": len(validated["findings"]), "uninvestigated_count": remaining}

    @staticmethod
    def validated_notes(packet, notes):
        validated = validate_notes(packet, notes)
        directories = {f["finding_id"]: f["directory"] for f in packet["findings"]}
        for finding in validated["findings"]:
            finding["directory"] = directories[finding["finding_id"]]
        validated["uninvestigated_count"] = len(packet["findings"]) - len(validated["findings"])
        return validated

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                if session["status"] == "scanning" and session["process"]:
                    session["process"].terminate()
