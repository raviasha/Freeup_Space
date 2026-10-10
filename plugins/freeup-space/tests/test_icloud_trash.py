"""Regression checks for approved iCloud cleanup; never trash user data."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

PLUGIN_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PLUGIN_SRC))
from freeup_space import apply, trash_macos
from freeup_space.models import Candidate, CandidateSnapshot, CleanupPlan, Risk


class ICloudTrashTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin" and os.environ.get("FREEUP_NATIVE_TESTS") == "1",
                         "native smoke test explicitly enabled for test-created files only")
    def test_native_prepare_trash_and_restore_test_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / ('icloud-regression-' + root.name + '-"quotes"-\\-雪.txt')
            path.write_text("test fixture only")
            trash_macos.prepare_for_trash(root, "macos")
            trash_macos.move_to_trash(path, "macos")
            destination = Path.home() / ".Trash" / path.name
            try:
                self.assertFalse(path.exists())
                self.assertEqual(destination.read_text(), "test fixture only")
            finally:
                if destination.exists():
                    destination.replace(path)
            self.assertTrue(path.exists())

    @unittest.skipUnless(sys.platform == "darwin" and os.environ.get("FREEUP_NATIVE_TESTS") == "1",
                         "native smoke test explicitly enabled")
    def test_native_missing_item_returns_receipt_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "does-not-exist"
            for action in ("prepare", "trash"):
                with self.assertRaisesRegex(RuntimeError, "[Cc]annot inspect|could not move"):
                    trash_macos._native_operation(path, "macos", action, 5)

    def test_native_error_is_reported_instead_of_claiming_success(self):
        result = subprocess.CompletedProcess([], 0, '{"ok":false,"error":"download failed"}', "")
        with patch.object(trash_macos.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "download failed"):
                trash_macos.move_to_trash(Path('/example/a "quoted" file'), "macos")

    def test_native_script_cloud_behaviour(self):
        script = getattr(trash_macos, "NATIVE_SCRIPT", None)
        self.assertIsNotNone(script, "native iCloud preparation is missing")
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("macos_cloud_trash_test.js"))],
            input=script, text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def make_plan(self, directory):
        candidates = []
        for index in range(2):
            path = Path(directory).resolve() / ("file%d.txt" % index)
            path.write_text("unchanged")
            candidates.append(Candidate(
                candidate_id="OLD-%03d" % index, path=path, category="old-file",
                reasons=("old",), risk=Risk.MEDIUM, actionable=True,
                snapshot=CandidateSnapshot.from_path(path), reclaimable_bytes=9,
            ))
        return CleanupPlan("1", "run", "macos", datetime.now(timezone.utc),
                           "1", tuple(candidates), "d" * 64)

    def test_changed_during_download_is_not_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            def prepare(path, platform):
                path.write_text("changed during hydration")
            with patch.object(apply, "prepare_macos_trash", side_effect=prepare, create=True), \
                    patch.object(apply, "_move") as move:
                receipt = apply.apply_selection(plan, ["OLD-000"], "macos")
            self.assertFalse(receipt.moved)
            self.assertEqual(receipt.skipped[0].reason, "snapshot-mismatch")
            move.assert_not_called()

    def test_download_failure_does_not_block_other_approved_items(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            def prepare(path, platform):
                if path.name == "file0.txt":
                    raise RuntimeError("iCloud download timed out")
            def move(path, platform, size):
                return apply.MoveResult(path, True, size, "native-trash")
            with patch.object(apply, "prepare_macos_trash", side_effect=prepare, create=True), \
                    patch.object(apply, "_move", side_effect=move):
                receipt = apply.apply_selection(plan, ["OLD-000", "OLD-001"], "macos")
            self.assertEqual([x.candidate_id for x in receipt.moved], ["OLD-001"])
            self.assertEqual([x.candidate_id for x in receipt.failed], ["OLD-000"])
            self.assertIn("timed out", receipt.failed[0].reason)

    def test_dry_run_does_not_download_or_move(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            with patch.object(apply, "prepare_macos_trash", create=True) as prepare, \
                    patch.object(apply, "_move") as move:
                receipt = apply.apply_selection(plan, ["OLD-000"], "macos", dry_run=True)
            self.assertEqual(receipt.skipped[0].reason, "dry-run")
            prepare.assert_not_called()
            move.assert_not_called()


if __name__ == "__main__":
    unittest.main()
