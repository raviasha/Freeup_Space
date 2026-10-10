from pathlib import Path
import sys
import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def isolated_widget_state(tmp_path, monkeypatch):
    """Fixture servers must never read or write a real user's saved sessions."""
    monkeypatch.setenv("FREEUP_WIDGET_STATE_DIR", str(tmp_path / "widget-state"))
