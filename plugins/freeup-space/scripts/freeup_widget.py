"""Bundled stdio MCP Apps entry point; uses Python's standard library only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from freeup_space.widget_server import main

if __name__ == "__main__":
    main()
