"""Executable entry point shared by source smoke tests and PyInstaller builds."""
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from freeup_space.runtime import main

if __name__ == "__main__":
    raise SystemExit(main())
