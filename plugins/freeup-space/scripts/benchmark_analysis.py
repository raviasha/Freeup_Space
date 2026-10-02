"""Repeatable post-inventory benchmark with synthetic metadata and no content reads.

Run with --plugin pointing at a baseline or patched plugin. Duplicate content I/O
is replaced with an empty result so this measures classification and preparation,
not drive speed. All generated artifacts live in a temporary directory.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument("--plugin", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--records", type=int, default=10000)
args = parser.parse_args()
if args.records < 1:
    parser.error("records must be positive")
sys.path.insert(0, str(args.plugin / "src"))
from datetime import datetime, timezone
from freeup_space import cli, deep
from freeup_space.models import FileRecord, ScanRun

now = datetime(2026, 10, 2, tzinfo=timezone.utc)
root = Path.home() / "Library/Caches/freeup-synthetic-benchmark"
records = tuple(FileRecord(path=root / ("file-%06d.zip" % i), size=10000 + i % 20,
                          allocated_size=12288, mtime=now.timestamp() - (80000000 if i % 5 == 0 else 0),
                          atime=now.timestamp(), ctime=now.timestamp(),
                          st_dev=1, st_ino=i+1, volume_id="1", file_id="1:%d" % (i+1))
                for i in range(args.records))
run = ScanRun("benchmark", "macos", now, completed_at=now, files=records, mode="deep",
              coverage={"inventory_finished": True, "files_scanned": len(records), "duplicates": "pending"})
deep.inventory = lambda *args, **kwargs: run
deep.find_duplicates = lambda *args, **kwargs: ()
os.environ["FREEUP_WIDGET_PROGRESS"] = "1"
with tempfile.TemporaryDirectory(prefix="freeup-analysis-benchmark-") as workspace:
    previous = Path.cwd()
    try:
        os.chdir(workspace)
        output = io.StringIO()
        wall = time.perf_counter()
        cpu = time.process_time()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["start", "--mode", "deep", "--run-id", "benchmark", "--summary-only", workspace])
        result = json.loads(output.getvalue())
        print(json.dumps(dict(records=args.records, seconds=round(time.perf_counter()-wall, 3),
                              cpu_seconds=round(time.process_time()-cpu, 3),
                              peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                              artifact_bytes=sum(p.stat().st_size for p in Path(workspace).rglob("*") if p.is_file()),
                              candidate_count=result["candidate_count"], plan_digest=result["plan_digest"])))
    finally:
        os.chdir(previous)
