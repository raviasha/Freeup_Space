"""Argparse CLI for scan, report, apply, and permanent-delete workflows."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Optional, Sequence

from .apply import apply_selection
from .categories import classify
from .models import ActionType, CandidateSnapshot, DuplicateGroup, Evidence, FileRecord, Risk, ScanError, ScanRun
from .old_files import find_old_files
from .plans import PlanError, build_permanent_plan, build_plan
from .permanent_delete import ApprovalError, permanently_delete
from .policy import Policy
from .reports import (
    QUICK_CATEGORIES,
    quick_candidates,
    render_markdown,
    render_quick_markdown,
)
from .scanner import scan_paths


RUNS_DIR = Path(".freeup-space/runs")


class RunArtifactError(ValueError):
    """A stored run is missing, corrupt, or incomplete."""


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name("{}.tmp-{}".format(path.name, uuid.uuid4().hex))
    temp_path.write_text(text, encoding="utf-8")
    os.replace(temp_path, path)


def _run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", run_id):
        raise RunArtifactError("invalid run ID")
    return RUNS_DIR / run_id


def _run_path(run_id: str) -> Path:
    return _run_dir(run_id) / "run.json"


def _plan_path(run_id: str) -> Path:
    return _run_dir(run_id) / "plan.json"


def _report_path(run_id: str) -> Path:
    return _run_dir(run_id) / "report.md"


def _receipt_path(run_id: str) -> Path:
    return _run_dir(run_id) / "receipt.json"


def _default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "{}-{}".format(stamp, uuid.uuid4().hex[:8])


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RunArtifactError("missing run artifact: {}".format(path)) from error
    except json.JSONDecodeError as error:
        raise RunArtifactError("corrupt run artifact: {}".format(path)) from error


def _encode_run(run: ScanRun) -> dict:
    payload = run.to_dict()
    payload["started_at"] = run.started_at.isoformat()
    if run.completed_at is not None:
        payload["completed_at"] = run.completed_at.isoformat()
    payload["files"] = [file_record.to_dict() for file_record in run.files]
    return payload


def _decode_snapshot(data: dict) -> CandidateSnapshot:
    return CandidateSnapshot(
        st_dev=int(data["st_dev"]),
        st_ino=int(data["st_ino"]),
        size=int(data["size"]),
        mtime=float(data["mtime"]),
        digest=data.get("digest"),
        path=Path(data["path"]) if data.get("path") is not None else None,
        safe_root=Path(data["safe_root"]) if data.get("safe_root") is not None else None,
    )


def _decode_file_record(data: dict) -> FileRecord:
    return FileRecord(
        path=Path(data["path"]),
        size=int(data["size"]),
        allocated_size=data.get("allocated_size"),
        mtime=float(data["mtime"]),
        atime=float(data["atime"]),
        ctime=float(data["ctime"]),
        st_dev=int(data["st_dev"]),
        st_ino=int(data["st_ino"]),
        volume_id=str(data["volume_id"]),
        file_id=str(data["file_id"]),
        file_kind=data.get("file_kind", "regular"),
        owner=data.get("owner"),
        flags=tuple(data.get("flags", ())),
    )


def _decode_run(data: dict) -> ScanRun:
    try:
        files = tuple(_decode_file_record(item) for item in data.get("files", ()))
        return ScanRun(
            run_id=str(data["run_id"]),
            platform=str(data["platform"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at") is not None
                else None
            ),
            complete=bool(data.get("complete", False)),
            files=files,
            candidates=(),
            duplicate_groups=tuple(DuplicateGroup(
                group_id=item["group_id"], digest=item["digest"], size=int(item["size"]),
                retained_path=Path(item["retained_path"]),
                duplicate_paths=tuple(Path(path) for path in item["duplicate_paths"]),
                reclaimable_bytes=int(item["reclaimable_bytes"]),
            ) for item in data.get("duplicate_groups", ())),
            evidence=(),
            errors=tuple(ScanError(path=Path(item["path"]), operation=item["operation"],
                                   error_type=item["error_type"], message=item["message"])
                         for item in data.get("errors", ())),
            mode=data.get("mode", "scan"),
            coverage=data.get("coverage", {}),
        )
    except Exception as error:  # pragma: no cover - defensive against format drift
        raise RunArtifactError("corrupt run artifact: {}".format(data.get("run_id"))) from error


def _store_run(run: ScanRun) -> None:
    _atomic_write(_run_path(run.run_id), _json_dump(_encode_run(run)))


def _store_plan(plan) -> None:
    _atomic_write(_plan_path(plan.run_id), _json_dump(plan.to_dict()))


def _store_report(run_id: str, report: str) -> None:
    _atomic_write(_report_path(run_id), report)


def _store_receipt(run_id: str, receipt) -> None:
    _atomic_write(_receipt_path(run_id), _json_dump(receipt.to_dict()))


def _load_run(run_id: str) -> ScanRun:
    return _decode_run(_load_json(_run_path(run_id)))


def _collect_evidence(run: ScanRun, policy: Policy) -> tuple[Evidence, ...]:
    evidence = list(
        find_old_files(
            tuple(record for record in run.files if record.file_kind == "regular"),
            run.completed_at or run.started_at,
            policy=policy,
        )
    )
    established = {item.path for item in evidence}
    established.update(path for group in run.duplicate_groups for path in group.duplicate_paths)
    for record in run.files:
        evidence.extend(item for item in classify(record, policy)
                        if item.rule != "unclassified" or record.path not in established)
    return tuple(evidence)


def _build_plan(run: ScanRun, platform: str, permanent: bool = False):
    policy = Policy.for_platform(platform)
    evidence = _collect_evidence(run, policy)
    if permanent:
        return build_permanent_plan(run, evidence, policy)
    return build_plan(run, evidence, policy, action=ActionType.TRASH)


def cmd_scan(args: argparse.Namespace) -> int:
    run_id = args.run_id or _default_run_id()
    run = scan_paths(args.paths, Policy.for_platform(args.platform), lambda _: None, Event())
    run = replace(run, run_id=run_id)
    _store_run(run)
    print(run.run_id)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run = _load_run(args.run_id)
    if not run.complete:
        raise RunArtifactError("incomplete run cannot produce a plan: {}".format(args.run_id))
    plan = _build_plan(run, args.platform or run.platform, permanent=False)
    _store_plan(plan)
    full = getattr(args, "full", False) or run.mode in {"deep", "deep-ai"}
    limit = getattr(args, "limit", 10)
    summary_only = getattr(args, "summary_only", False)
    report = render_markdown(plan) if full else render_quick_markdown(plan, limit)
    if run.mode in {"deep", "deep-ai"}:
        from .deep import coverage_markdown
        report = coverage_markdown(run) + report
    _store_report(run.run_id, report)
    if summary_only:
        actionable = tuple(
            item
            for item in plan.candidates
            if item.actionable and item.risk is not Risk.REPORT_ONLY
        )
        selected = actionable if full else quick_candidates(plan, limit)
        payload = {
            "run_id": run.run_id,
            "mode": "full" if full else "quick",
            "scan_mode": run.mode,
            "coverage": run.coverage,
            "report_path": str(_report_path(run.run_id).resolve()),
            "candidate_count": len(selected),
            "reclaimable_bytes": sum(item.reclaimable_bytes for item in selected),
        }
        if not full:
            payload["candidates"] = [
                {
                    "candidate_id": item.candidate_id,
                    "path": str(item.path),
                    "reclaimable_bytes": item.reclaimable_bytes,
                    "risk": item.risk.value,
                    "category": item.category,
                    "action": item.proposed_action.value,
                    "recoverable": item.proposed_action is ActionType.TRASH,
                }
                for item in selected
            ]
        print(
            _json_dump(payload),
            end="",
        )
    else:
        sys.stdout.write(report)
    return 0


def _default_quick_roots(platform: str) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    home = Path.home()
    if platform == "macos":
        cache_roots = (home / "Library" / "Caches", home / "Library" / "Logs", home / ".cache")
    elif platform == "windows":
        cache_roots = (home / "AppData" / "Local" / "Temp", home / ".cache")
    else:
        Policy.for_platform(platform)
        raise AssertionError("unreachable")
    file_roots = (home / "Downloads", home / "Documents", home / "Desktop")
    return cache_roots, file_roots


def _scan_before_deadline(
    path: Path, policy: Policy, deadline: float
) -> tuple[ScanRun, int]:
    cancel = Event()
    skipped_links = 0

    def progress(update):
        nonlocal skipped_links
        skipped_links = max(skipped_links, update.skipped_links)
        if monotonic() >= deadline:
            cancel.set()

    return scan_paths((path,), policy, progress, cancel), skipped_links


def _directory_record(path: Path, contents: ScanRun) -> FileRecord:
    value = path.lstat()
    allocated = sum(
        record.allocated_size if record.allocated_size is not None else record.size
        for record in contents.files
    )
    return FileRecord(
        path=path,
        size=int(value.st_size),
        allocated_size=allocated,
        mtime=float(value.st_mtime),
        atime=float(value.st_atime),
        ctime=float(value.st_ctime),
        st_dev=int(value.st_dev),
        st_ino=int(value.st_ino),
        volume_id=str(value.st_dev),
        file_id="{}:{}".format(value.st_dev, value.st_ino),
        file_kind="directory",
    )


def _quick_file(record: FileRecord, policy: Policy) -> bool:
    findings = classify(record, policy)
    return any(
        item.category in QUICK_CATEGORIES
        and item.actionable
        and item.risk in {Risk.LOW, Risk.MEDIUM}
        for item in findings
    )


def _developer_artifact_root(path: Path) -> Optional[Path]:
    names = {"build", "deriveddata", "dist", "node_modules"}
    for parent in reversed(path.parents):
        if parent.name.casefold() in names:
            return parent
    return None


def _quick_records_from_scan(scanned: ScanRun, policy: Policy) -> tuple[FileRecord, ...]:
    singles = []
    for record in scanned.files:
        developer_root = _developer_artifact_root(record.path)
        if developer_root is None and _quick_file(record, policy):
            singles.append(record)
    return tuple(singles)


def _is_active_codex_cache(path: Path) -> bool:
    home = Path.home()
    return path in {
        home / ".cache" / "codex-runtimes",
        home / "Library" / "Caches" / "Codex",
    }


def _smart_quick_scan(
    platform: str, run_id: str, budget_seconds: float
) -> tuple[ScanRun, tuple[str, ...], bool]:
    if budget_seconds <= 0:
        raise RunArtifactError("quick scan time budget must be positive")
    policy = Policy.for_platform(platform)
    cache_roots, file_roots = _default_quick_roots(platform)
    started_at = datetime.now(timezone.utc)
    deadline = monotonic() + budget_seconds
    records = []
    skipped = []

    for root in cache_roots:
        if monotonic() >= deadline:
            break
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir(), key=lambda item: str(item))
        except OSError:
            skipped.append(str(root))
            continue
        for path in entries:
            if monotonic() >= deadline:
                break
            try:
                path_stat = path.lstat()
            except OSError:
                skipped.append(str(path))
                continue
            if stat.S_ISLNK(path_stat.st_mode):
                skipped.append(str(path))
                continue
            if _is_active_codex_cache(path):
                skipped.append(str(path))
                continue
            scanned, skipped_links = _scan_before_deadline(path, policy, deadline)
            if not scanned.complete or skipped_links:
                skipped.append(str(path))
                continue
            if stat.S_ISDIR(path_stat.st_mode):
                record = _directory_record(path, scanned)
                if record.allocated_size:
                    records.append(record)
            else:
                records.extend(
                    record for record in scanned.files if _quick_file(record, policy)
                )

    for root in file_roots:
        if monotonic() >= deadline:
            break
        if not root.exists():
            continue
        scanned, _ = _scan_before_deadline(root, policy, deadline)
        if not scanned.complete:
            skipped.append(str(root))
            continue
        records.extend(_quick_records_from_scan(scanned, policy))

    unique = {}
    for record in records:
        unique.setdefault((record.st_dev, record.st_ino), record)
    completed_at = datetime.now(timezone.utc)
    run = ScanRun(
        run_id=run_id,
        platform=platform,
        started_at=started_at,
        completed_at=completed_at,
        complete=True,
        files=tuple(sorted(unique.values(), key=lambda item: str(item.path))),
    )
    return run, tuple(skipped), monotonic() >= deadline


def cmd_quick(args: argparse.Namespace) -> int:
    run_id = args.run_id or _default_run_id()
    paths = tuple(args.paths)
    skipped = ()
    timed_out = False
    if paths:
        missing = tuple(path for path in paths if not path.exists())
        if missing:
            raise RunArtifactError(
                "quick scan path does not exist: {}".format(
                    ", ".join(str(path) for path in missing)
                )
            )
        run = scan_paths(paths, Policy.for_platform(args.platform), lambda _: None, Event())
        run = replace(run, run_id=run_id)
    else:
        run, skipped, timed_out = _smart_quick_scan(
            args.platform, run_id, args.budget_seconds
        )
    run = replace(run, mode="quick", coverage=dict(run.coverage,
                  status="limited", skipped_paths=list(skipped), time_budget_exhausted=timed_out))
    _store_run(run)
    if not run.complete:
        raise RunArtifactError(
            "quick scan encountered filesystem errors; run a narrower explicit path or grant the required platform access: {}".format(
                run_id
            )
        )
    plan = _build_plan(run, args.platform, permanent=False)
    _store_plan(plan)
    report = render_quick_markdown(plan, args.limit)
    _store_report(run.run_id, report)
    if args.summary_only:
        selected = quick_candidates(plan, args.limit)
        print(
            _json_dump(
                {
                    "run_id": run.run_id,
                    "mode": "quick",
                    "mode_name": "Quick Scan",
                    "coverage": run.coverage,
                    "report_path": str(_report_path(run.run_id).resolve()),
                    "candidate_count": len(selected),
                    "reclaimable_bytes": sum(
                        item.reclaimable_bytes for item in selected
                    ),
                    "candidates": [
                        {
                            "candidate_id": item.candidate_id,
                            "path": str(item.path),
                            "reclaimable_bytes": item.reclaimable_bytes,
                            "risk": item.risk.value,
                            "category": item.category,
                            "action": item.proposed_action.value,
                            "recoverable": item.proposed_action is ActionType.TRASH,
                        }
                        for item in selected
                    ],
                    "skipped_path_count": len(skipped),
                    "time_budget_exhausted": timed_out,
                }
            ),
            end="",
        )
    else:
        sys.stdout.write(report)
    return 0


def _selection_ids(args: argparse.Namespace) -> tuple[str, ...]:
    if not args.ids:
        raise RunArtifactError("explicit candidate IDs are required")
    return tuple(args.ids)


def cmd_apply(args: argparse.Namespace) -> int:
    run = _load_run(args.run_id)
    if not run.complete:
        raise RunArtifactError("incomplete run cannot be applied: {}".format(args.run_id))
    plan = _build_plan(run, args.platform or run.platform, permanent=False)
    if run.mode in {"quick", "deep", "deep-ai"}:
        reviewed = _load_json(_plan_path(run.run_id))
        if reviewed.get("digest") != plan.digest:
            raise RunArtifactError("plan changed; generate and review a fresh report before cleanup")
    _store_plan(plan)
    receipt = apply_selection(plan, _selection_ids(args), args.platform or run.platform, dry_run=args.dry_run)
    _store_receipt(run.run_id, receipt)
    sys.stdout.write(_json_dump(receipt.to_dict()))
    return 0


def cmd_permanent_delete(args: argparse.Namespace) -> int:
    run = _load_run(args.run_id)
    if not run.complete:
        raise RunArtifactError("incomplete run cannot be applied: {}".format(args.run_id))
    plan = _build_plan(run, args.platform or run.platform, permanent=True)
    _store_plan(plan)
    receipt = permanently_delete(plan, _selection_ids(args), args.platform or run.platform)
    _store_receipt(run.run_id, receipt)
    sys.stdout.write(_json_dump(receipt.to_dict()))
    return 0


def cmd_modes(args=None) -> int:
    from .deep import MODES
    print(_json_dump({"status": "choose-mode", "prompt": "Choose your scan mode", "modes": MODES}), end="")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    from .deep import MODE_NAMES, analyze, coverage_markdown, default_roots, inventory
    from .investigation import make_packet
    if args.mode is None:
        return cmd_modes()
    if not 1 <= args.limit <= 100 or not 1 <= args.ai_limit <= 100:
        raise RunArtifactError("shortlist and investigation limits must be between 1 and 100")
    native = "windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "unsupported"
    if args.platform != native:
        raise RunArtifactError("run this scan on the selected operating system")
    if args.mode == "quick":
        if args.resume:
            raise RunArtifactError("Quick Scan cannot resume; choose a deep mode")
        if args.run_id and _run_dir(args.run_id).exists():
            raise RunArtifactError("run ID already exists; choose a new ID")
        return cmd_quick(args)
    if args.resume:
        if args.paths or args.run_id:
            raise RunArtifactError("resume cannot be combined with new paths or a run ID")
        run = _load_run(args.resume)
        if run.mode != args.mode or run.platform != args.platform:
            raise RunArtifactError("resume must use the original scan mode and platform")
        if not run.coverage.get("inventory_finished"):
            raise RunArtifactError("inventory did not finish; start a fresh scan")
    else:
        run_id = args.run_id or _default_run_id()
        if _run_dir(run_id).exists():
            raise RunArtifactError("run ID already exists; choose a new ID or resume")
        paths = tuple(args.paths) or default_roots(args.platform)
        if any(not path.exists() for path in paths):
            raise RunArtifactError("one or more scan roots do not exist")
        run = inventory(paths, args.platform, run_id, args.mode)
        _store_run(run)  # Resume can reuse this inventory if hashing is interrupted.
    if not run.complete:
        # Publish a safe inventory checkpoint before the expensive duplicate pass.
        # Applying is rejected while analysis_pending is set.
        preliminary_run = replace(
            run,
            complete=True,
            coverage=dict(run.coverage, analysis_pending=True,
                           analysis_stage="inventory-complete",
                           duplicates="pending"),
        )
        preliminary_plan = _build_plan(preliminary_run, preliminary_run.platform)
        _store_plan(preliminary_plan)
        _store_report(
            preliminary_run.run_id,
            coverage_markdown(preliminary_run) + render_markdown(preliminary_plan),
        )
        if args.summary_only:
            provisional = tuple(
                c for c in preliminary_plan.candidates
                if c.actionable and c.risk is not Risk.REPORT_ONLY
            )
            print(_json_dump({
                "event": "partial-results",
                "run_id": preliminary_run.run_id,
                "mode": preliminary_run.mode,
                "status": "inventory-complete-analysis-pending",
                "report_path": str(_report_path(preliminary_run.run_id).resolve()),
                "candidate_count": len(provisional),
                "duplicates": "pending",
                "message": "Initial candidates are ready for review; exact duplicate analysis is still running.",
                "candidates": [
                    {"candidate_id": c.candidate_id, "path": str(c.path),
                     "category": c.category, "risk": c.risk.value,
                     "reclaimable_bytes": c.reclaimable_bytes}
                    for c in provisional[:args.limit]
                ],
            }), file=sys.stderr, flush=True)
        run = analyze(run)
        _store_run(run)
    plan = _build_plan(run, run.platform)
    _store_plan(plan)
    _store_report(run.run_id, coverage_markdown(run) + render_markdown(plan))
    actionable = tuple(c for c in plan.candidates if c.actionable and c.risk is not Risk.REPORT_ONLY)
    categories = {}
    for candidate in plan.candidates:
        group = categories.setdefault(candidate.category, {"count": 0, "actionable_bytes": 0})
        group["count"] += 1
        group["actionable_bytes"] += candidate.reclaimable_bytes
    payload = {"run_id": run.run_id, "mode": run.mode, "mode_name": MODE_NAMES[run.mode],
               "status": "ready-for-review", "plan_digest": plan.digest,
               "report_path": str(_report_path(run.run_id).resolve()),
               "candidate_count": len(plan.candidates), "actionable_count": len(actionable),
               "reclaimable_bytes": sum(c.reclaimable_bytes for c in actionable),
               "categories": categories, "coverage": {k: v for k, v in run.coverage.items()
                                                        if k != "excluded"},
               "candidates": [{"candidate_id": c.candidate_id, "path": str(c.path),
                               "category": c.category, "risk": c.risk.value,
                               "reclaimable_bytes": c.reclaimable_bytes} for c in actionable[:args.limit]]}
    if run.mode == "deep-ai":
        packet_path = _run_dir(run.run_id) / "investigation.json"
        # Preserve stable finding IDs and previously recorded notes when resuming.
        if not packet_path.exists():
            packet = make_packet(run, plan, args.ai_limit)
            _atomic_write(packet_path, _json_dump(packet))
        else:
            packet = _load_json(packet_path)
        payload["ai_investigation"] = {"status": packet["status"],
                                      "packet_path": str(packet_path.resolve()),
                                      "finding_count": len(packet["findings"]),
                                      "file_contents_included": False}
    if args.summary_only:
        print(_json_dump(payload), end="")
    else:
        print(_report_path(run.run_id).read_text(encoding="utf-8"))
        if run.mode == "deep-ai":
            print("AI investigation pending in Codex; metadata packet: " + payload["ai_investigation"]["packet_path"])
    return 0


def cmd_investigate(args: argparse.Namespace) -> int:
    run = _load_run(args.run_id)
    if run.mode != "deep-ai" or not run.complete:
        raise RunArtifactError("investigation requires a completed Deep Scan + AI run")
    packet = _load_json(_run_dir(run.run_id) / "investigation.json")
    if args.finding_id:
        found = [item for item in packet["findings"] if item["finding_id"] == args.finding_id]
        if not found:
            raise RunArtifactError("unknown investigation finding ID")
        packet = dict(packet, findings=found)
    print(_json_dump(packet), end="")
    return 0


def cmd_record_investigation(args: argparse.Namespace) -> int:
    from .investigation import validate_notes
    run = _load_run(args.run_id)
    if run.mode != "deep-ai" or not run.complete:
        raise RunArtifactError("AI notes require a completed Deep Scan + AI run")
    packet = _load_json(_run_dir(run.run_id) / "investigation.json")
    if args.input.stat().st_size > 512_000:
        raise RunArtifactError("AI notes exceed the size limit")
    notes = validate_notes(packet, _load_json(args.input))
    destination = _run_dir(run.run_id) / "ai-notes.json"
    _atomic_write(destination, _json_dump(notes))
    print(_json_dump({"status": "recorded", "path": str(destination.resolve()),
                      "finding_count": len(notes["findings"]), "cleanup_plan_changed": False}), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="freeup-space")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("modes", help="show the three scan choices without scanning").set_defaults(func=cmd_modes)
    start = subparsers.add_parser("start", help="choose Quick Scan, Deep Scan, or Deep Scan + AI")
    start.add_argument("paths", nargs="*", type=Path)
    start.add_argument("--mode", choices=("quick", "deep", "deep-ai"))
    start.add_argument("--platform", choices=("macos", "windows"), default="windows" if os.name == "nt" else "macos")
    start.add_argument("--run-id")
    start.add_argument("--resume", metavar="RUN_ID")
    start.add_argument("--limit", type=int, default=10)
    start.add_argument("--ai-limit", type=int, default=20)
    start.add_argument("--budget-seconds", type=float, default=45.0)
    start.add_argument("--summary-only", action="store_true")
    start.set_defaults(func=cmd_start)

    investigate = subparsers.add_parser("investigate", help="read a metadata-only AI investigation packet")
    investigate.add_argument("--run-id", required=True)
    investigate.add_argument("--finding-id")
    investigate.set_defaults(func=cmd_investigate)

    notes = subparsers.add_parser("record-investigation", help="store advisory AI conclusions separately")
    notes.add_argument("--run-id", required=True)
    notes.add_argument("--input", required=True, type=Path)
    notes.set_defaults(func=cmd_record_investigation)

    quick = subparsers.add_parser(
        "quick", help="run a time-bounded high-value scan and print a deterministic shortlist"
    )
    quick.add_argument("paths", nargs="*", type=Path)
    quick.add_argument(
        "--platform", default="windows" if sys.platform.startswith("win") else "macos"
    )
    quick.add_argument("--run-id")
    quick.add_argument("--limit", type=int, default=10)
    quick.add_argument("--budget-seconds", type=float, default=45.0)
    quick.add_argument("--summary-only", action="store_true")
    quick.set_defaults(func=cmd_quick)

    scan = subparsers.add_parser("scan")
    scan.add_argument("paths", nargs="+", type=Path)
    scan.add_argument("--platform", default="windows" if sys.platform.startswith("win") else "macos")
    scan.add_argument("--run-id")
    scan.set_defaults(func=cmd_scan)

    report = subparsers.add_parser("report")
    report.add_argument("--run-id", required=True)
    report.add_argument("--platform")
    report.add_argument("--full", action="store_true")
    report.add_argument("--limit", type=int, default=10)
    report.add_argument("--summary-only", action="store_true")
    report.set_defaults(func=cmd_report)

    apply_cmd = subparsers.add_parser("apply")
    apply_cmd.add_argument("--run-id", required=True)
    apply_cmd.add_argument("--platform")
    apply_cmd.add_argument("--dry-run", action="store_true")
    apply_cmd.add_argument("ids", nargs="+")
    apply_cmd.set_defaults(func=cmd_apply)

    permanent = subparsers.add_parser("permanent-delete")
    permanent.add_argument("--run-id", required=True)
    permanent.add_argument("--platform")
    permanent.add_argument("ids", nargs="+")
    permanent.set_defaults(func=cmd_permanent_delete)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if not (sys.argv[1:] if argv is None else argv):
        return cmd_modes()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        parser.exit(130, "Scan interrupted. If inventory finished, resume the deep run with --resume RUN_ID.\n")
    except (RunArtifactError, PlanError, ApprovalError, ValueError, OSError) as error:
        parser.exit(2, "{}\n".format(error))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
