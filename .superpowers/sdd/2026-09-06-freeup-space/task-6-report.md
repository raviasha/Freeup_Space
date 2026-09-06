# Task 6 Report

Implemented the revalidation and native recoverable-deletion flow for the existing `freeup_space` package.

What changed:

- Added `src/freeup_space/revalidate.py` to re-check approved candidates immediately before mutation.
- Added `src/freeup_space/trash_macos.py` with a native Finder Trash adapter and an `empty_trash()` stub that is never used by the workflow.
- Added `src/freeup_space/trash_windows.py` with a Windows Shell Recycle Bin adapter.
- Added `src/freeup_space/apply.py` to enforce explicit ID selection, revalidation, dry-run support, trash moves, and receipt generation.
- Added focused regression tests in `tests/test_revalidate.py` and `tests/test_apply.py`.
- Added `tests/conftest.py` so pytest can import the existing `src/` layout.

Verification:

- `pytest tests/test_revalidate.py tests/test_apply.py -q`
- Result: `3 passed`

Notes:

- Modified files are skipped with the normalized reason `snapshot-mismatch`.
- The dry-run path never calls trash-emptying logic.
- The normal action path uses recoverable deletion adapters only and does not fall back to permanent deletion.
