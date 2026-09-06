import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from freeup_space import scanner
from freeup_space.policy import Policy
from freeup_space.scanner import FilesystemAdapter, scan_paths


class PermissionFaultAdapter(FilesystemAdapter):
    def __init__(self, blocked: Path):
        self.blocked = blocked

    def scan_directory(self, path: Path, expected_stat):
        if path == self.blocked:
            raise PermissionError("fixture denied access")
        return super().scan_directory(path, expected_stat)

    def scandir(self, path: Path):
        if path == self.blocked:
            raise PermissionError("fixture denied access")
        return super().scandir(path)


class ReparseFaultAdapter(FilesystemAdapter):
    def __init__(self, reparse_path: Path):
        self.reparse_path = reparse_path

    def stat_entry(self, directory: Path, entry):
        result = super().stat_entry(directory, entry)
        if directory / entry.name == self.reparse_path:
            values = {
                name: getattr(result, name)
                for name in dir(result)
                if name.startswith("st_")
            }
            values["st_file_attributes"] = 0x400
            return SimpleNamespace(**values)
        return result


def test_scanner_does_not_follow_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "file.bin").write_bytes(b"data")
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)

    run = scan_paths(
        [tmp_path], Policy.for_platform("macos"), lambda _: None, Event()
    )

    assert sum(1 for file in run.files if file.path.name == "file.bin") == 1


def test_permission_error_is_recorded_and_scan_continues(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "secret.txt").write_text("secret")
    ok = tmp_path / "ok.txt"
    ok.write_text("visible")

    run = scan_paths(
        [tmp_path],
        Policy.for_platform("macos"),
        lambda _: None,
        Event(),
        adapter=PermissionFaultAdapter(blocked),
    )

    assert {error.path for error in run.errors} == {blocked}
    assert {file.path for file in run.files} == {ok}
    assert run.complete is False


def test_scanner_excludes_windows_reparse_points(tmp_path):
    junction = tmp_path / "junction"
    junction.mkdir()
    (junction / "nested.bin").write_bytes(b"not visited")
    ordinary = tmp_path / "ordinary.bin"
    ordinary.write_bytes(b"visited")

    run = scan_paths(
        [tmp_path],
        Policy.for_platform("windows"),
        lambda _: None,
        Event(),
        adapter=ReparseFaultAdapter(junction),
    )

    assert {file.path for file in run.files} == {ordinary}


def test_directory_replaced_by_symlink_after_lstat_is_not_traversed(tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "inside.bin").write_bytes(b"inside")
    outside = tmp_path.parent / "outside-race-fixture"
    outside.mkdir(exist_ok=True)
    outside_file = outside / "outside.bin"
    outside_file.write_bytes(b"outside")

    class ReplacementRaceAdapter(FilesystemAdapter):
        replaced = False

        def scan_directory(self, path: Path, expected_stat):
            if path == victim and not self.replaced:
                self.replaced = True
                victim.rename(tmp_path / "original-victim")
                victim.symlink_to(outside, target_is_directory=True)
            return super().scan_directory(path, expected_stat)

    run = scan_paths(
        [tmp_path],
        Policy.for_platform("macos"),
        lambda _: None,
        Event(),
        adapter=ReplacementRaceAdapter(),
    )

    assert outside_file not in {file.path for file in run.files}
    assert not any(file.path.parent == victim for file in run.files)
    assert run.complete is False
    assert {error.path for error in run.errors} == {victim}


def test_ancestor_replaced_by_symlink_to_same_tree_is_not_traversed(tmp_path):
    ancestor = tmp_path / "a"
    child = ancestor / "b"
    child.mkdir(parents=True)
    payload = child / "payload.bin"
    payload.write_bytes(b"outside after move")
    moved = tmp_path.parent / "moved-ancestor-race-fixture"

    class AncestorReplacementAdapter(FilesystemAdapter):
        replaced = False

        def scan_directory(self, path: Path, expected_stat):
            if path == child and not self.replaced:
                self.replaced = True
                ancestor.rename(moved)
                ancestor.symlink_to(moved, target_is_directory=True)
            return super().scan_directory(path, expected_stat)

    run = scan_paths(
        [tmp_path],
        Policy.for_platform("macos"),
        lambda _: None,
        Event(),
        adapter=AncestorReplacementAdapter(),
    )

    assert not any(file.path.name == "payload.bin" for file in run.files)
    assert run.complete is False
    assert run.errors


def test_fallback_fails_closed_when_no_race_resistant_directory_api(
    tmp_path, monkeypatch
):
    ancestor = tmp_path / "a"
    child = ancestor / "b"
    child.mkdir(parents=True)
    (child / "payload.bin").write_bytes(b"outside after move")
    moved = tmp_path.parent / "moved-fallback-race-fixture"
    monkeypatch.setattr(scanner.os, "supports_fd", set())

    class AncestorReplacementAdapter(FilesystemAdapter):
        replaced = False

        def scan_directory(self, path: Path, expected_stat):
            if path == child and not self.replaced:
                self.replaced = True
                ancestor.rename(moved)
                ancestor.symlink_to(moved, target_is_directory=True)
            return super().scan_directory(path, expected_stat)

    run = scan_paths(
        [tmp_path],
        Policy.for_platform("windows"),
        lambda _: None,
        Event(),
        adapter=AncestorReplacementAdapter(),
    )

    assert not any(file.path.name == "payload.bin" for file in run.files)
    assert run.complete is False
    assert run.errors[0].operation == "unsupported-no-follow"


def test_fallback_does_not_accept_entries_from_swap_restored_during_scandir(
    tmp_path, monkeypatch
):
    ancestor = tmp_path / "a"
    ancestor.mkdir()
    (ancestor / "inside.bin").write_bytes(b"inside")
    outside = tmp_path.parent / "outside-aba-race-fixture"
    outside.mkdir()
    (outside / "outside-only.bin").write_bytes(b"outside")
    moved = tmp_path.parent / "moved-aba-race-fixture"
    monkeypatch.setattr(scanner.os, "supports_fd", set())

    class SwapAndRestoreIterator:
        def __init__(self, path: Path):
            ancestor.rename(moved)
            ancestor.symlink_to(outside, target_is_directory=True)
            self.iterator = os.scandir(path)
            self.restored = False

        def __enter__(self):
            return self

        def __iter__(self):
            return self

        def __next__(self):
            try:
                return next(self.iterator)
            except StopIteration:
                self.restore()
                raise

        def __exit__(self, error_type, error, traceback):
            self.restore()

        def restore(self):
            if self.restored:
                return
            self.iterator.close()
            ancestor.unlink()
            moved.rename(ancestor)
            self.restored = True

    class SwapAndRestoreAdapter(FilesystemAdapter):
        scandir_attempted = False

        def scandir(self, path: Path):
            if path == ancestor:
                self.scandir_attempted = True
                return SwapAndRestoreIterator(path)
            return super().scandir(path)

    adapter = SwapAndRestoreAdapter()
    run = scan_paths(
        [tmp_path],
        Policy.for_platform("windows"),
        lambda _: None,
        Event(),
        adapter=adapter,
    )

    assert not any(file.path.name == "outside-only.bin" for file in run.files)
    assert run.complete is False
    assert run.errors[0].operation == "unsupported-no-follow"
    assert adapter.scandir_attempted is False


def test_scanner_cancellation_marks_run_incomplete_and_reports_final_progress(tmp_path):
    (tmp_path / "file.bin").write_bytes(b"data")
    cancel = Event()
    cancel.set()
    updates = []

    run = scan_paths(
        [tmp_path], Policy.for_platform("macos"), updates.append, cancel
    )

    assert run.files == ()
    assert run.complete is False
    assert run.completed_at is not None
    assert updates[-1].complete is False
    assert updates[-1].cancelled is True


def test_scanner_reports_metadata_progress_and_never_opens_file_contents(tmp_path):
    target = tmp_path / "payload.bin"
    target.write_bytes(b"payload")
    before = target.read_bytes()
    updates = []

    run = scan_paths(
        [tmp_path], Policy.for_platform("macos"), updates.append, Event()
    )

    assert len(run.files) == 1
    record = run.files[0]
    source_stat = os.lstat(target)
    assert record.path == target
    assert record.size == 7
    assert record.file_id == "{}:{}".format(source_stat.st_dev, source_stat.st_ino)
    assert record.volume_id == str(source_stat.st_dev)
    assert record.mtime == source_stat.st_mtime
    assert target.read_bytes() == before
    assert updates[-1].files_scanned == 1
    assert updates[-1].bytes_scanned == 7
    assert updates[-1].complete is True


def test_overlapping_roots_do_not_duplicate_physical_files(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    target = child / "only-once.bin"
    target.write_bytes(b"x")

    run = scan_paths(
        [tmp_path, child], Policy.for_platform("macos"), lambda _: None, Event()
    )

    assert [file.path for file in run.files] == [target]


def test_error_collection_is_bounded(tmp_path):
    blocked = []
    for index in range(3):
        path = tmp_path / "blocked-{}".format(index)
        path.mkdir()
        blocked.append(path)

    class ManyPermissionFaults(FilesystemAdapter):
        def scan_directory(self, path: Path, expected_stat):
            if path in blocked:
                raise PermissionError("denied")
            return super().scan_directory(path, expected_stat)

    run = scan_paths(
        [tmp_path],
        Policy.for_platform("macos"),
        lambda _: None,
        Event(),
        adapter=ManyPermissionFaults(),
        max_errors=2,
    )

    assert len(run.errors) == 2
    assert run.complete is False


def test_unrecorded_error_after_limit_still_marks_scan_incomplete(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir()

    run = scan_paths(
        [tmp_path],
        Policy.for_platform("macos"),
        lambda _: None,
        Event(),
        adapter=PermissionFaultAdapter(blocked),
        max_errors=0,
    )

    assert run.errors == ()
    assert run.complete is False
