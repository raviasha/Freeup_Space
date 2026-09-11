"""Packaging entry point outside scripts/, which also contains freeup_space.py."""
from freeup_space.runtime import main

if __name__ == '__main__':
    raise SystemExit(main())
