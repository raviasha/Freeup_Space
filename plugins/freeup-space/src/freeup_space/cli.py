"""Argparse CLI for scan, report, apply, and permanent-delete workflows."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Sequence

from .apply import apply_selection
from .categories import classify
from .models import ActionType, CandidateSnapshot, Evidence, FileRecord, ScanRun
from .old_files import find_old_files
from .plans import PlanError, build_permanent_plan, build_plan
from .permanent_delete import ApprovalError, permanently_delete
from .policy import Policy
from .reports import render_markdown
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
            duplicate_groups=(),
            evidence=(),
            errors=(),
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
    evidence = []
    for record in run.files:
        if record.file_kind != "regular":
            continue
        evidence.extend(classify(record, policy))
    evidence.extend(find_old_files(run.files, run.completed_at or run.started_at))
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
    report = render_markdown(plan)
    _store_report(run.run_id, report)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="freeup-space")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan")
    scan.add_argument("paths", nargs="+", type=Path)
    scan.add_argument("--platform", default="windows" if sys.platform.startswith("win") else "macos")
    scan.add_argument("--run-id")
    scan.set_defaults(func=cmd_scan)

    report = subparsers.add_parser("report")
    report.add_argument("--run-id", required=True)
    report.add_argument("--platform")
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
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (RunArtifactError, PlanError, ApprovalError) as error:
        parser.exit(2, "{}\n".format(error))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
