import json
import os
import subprocess
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]


def test_manifest_has_required_identity():
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text())
    assert manifest["name"] == "freeup-space"
    assert manifest["version"]
    assert "description" in manifest


def test_marketplace_points_at_plugin():
    catalog = json.loads((REPO_ROOT / ".agents/plugins/marketplace.json").read_text())
    entry = next(p for p in catalog["plugins"] if p["name"] == "freeup-space")
    assert entry["source"]["path"] == "./plugins/freeup-space"
    assert entry["policy"]["installation"] == "AVAILABLE"
    assert entry["policy"]["authentication"] == "ON_INSTALL"
    assert entry["category"] == "Utilities"


def test_package_imports_from_plugin_root():
    result = subprocess.run(
        [sys.executable, "-c", "import freeup_space; print(freeup_space.__version__)"],
        cwd=PLUGIN_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(PLUGIN_ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.1.0"
