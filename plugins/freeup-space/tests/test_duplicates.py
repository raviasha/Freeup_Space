import os
from pathlib import Path

from freeup_space.duplicates import find_duplicates, sha256_hasher
from freeup_space.models import FileRecord


def make_file(path: Path, content: bytes) -> FileRecord:
    path.write_bytes(content)
    source = path.stat()
    return FileRecord(
        path=path,
        size=source.st_size,
        allocated_size=getattr(source, "st_blocks", 0) * 512,
        mtime=source.st_mtime,
        atime=source.st_atime,
        ctime=source.st_ctime,
        st_dev=source.st_dev,
        st_ino=source.st_ino,
        volume_id=str(source.st_dev),
        file_id="{}:{}".format(source.st_dev, source.st_ino),
    )


def test_same_size_different_content_is_not_duplicate(tmp_path):
    a = make_file(tmp_path / "a.bin", b"aaaa")
    b = make_file(tmp_path / "b.bin", b"bbbb")

    assert find_duplicates([a, b], sha256_hasher) == []


def test_exact_content_match_is_confirmed_with_sha256(tmp_path):
    a = make_file(tmp_path / "a.bin", b"same content")
    b = make_file(tmp_path / "renamed.bin", b"same content")

    groups = find_duplicates([b, a], sha256_hasher)

    assert len(groups) == 1
    assert groups[0].digest == sha256_hasher(a.path)
    assert groups[0].retained_path == a.path
    assert groups[0].duplicate_paths == (b.path,)
    assert groups[0].reclaimable_bytes == len(b"same content")


def test_hard_link_aliases_are_not_counted_as_duplicate_copies(tmp_path):
    original = make_file(tmp_path / "original.bin", b"same")
    alias_path = tmp_path / "alias.bin"
    os.link(original.path, alias_path)
    alias = make_file_record(alias_path)

    assert find_duplicates([original, alias], sha256_hasher) == []


def make_file_record(path: Path) -> FileRecord:
    source = path.stat()
    return FileRecord(
        path=path,
        size=source.st_size,
        allocated_size=getattr(source, "st_blocks", 0) * 512,
        mtime=source.st_mtime,
        atime=source.st_atime,
        ctime=source.st_ctime,
        st_dev=source.st_dev,
        st_ino=source.st_ino,
        volume_id=str(source.st_dev),
        file_id="{}:{}".format(source.st_dev, source.st_ino),
    )


def test_partial_fingerprint_collision_is_resolved_by_full_hash(tmp_path):
    edge = b"x" * (64 * 1024)
    a = make_file(tmp_path / "a.bin", edge + b"aaaa" + edge)
    b = make_file(tmp_path / "b.bin", edge + b"bbbb" + edge)

    assert find_duplicates([a, b], sha256_hasher) == []


def test_full_hasher_runs_only_for_partial_fingerprint_survivors(tmp_path):
    a = make_file(tmp_path / "a.bin", b"same")
    b = make_file(tmp_path / "b.bin", b"same")
    c = make_file(tmp_path / "c.bin", b"nope")
    calls = []

    def tracking_hasher(path: Path) -> str:
        calls.append(path)
        return sha256_hasher(path)

    groups = find_duplicates([a, b, c], tracking_hasher)

    assert len(groups) == 1
    assert calls == [a.path, b.path]


def test_hash_read_failure_cannot_declare_an_exact_duplicate(tmp_path):
    a = make_file(tmp_path / "a.bin", b"same")
    b = make_file(tmp_path / "b.bin", b"same")

    def failing_hasher(path: Path) -> str:
        if path == b.path:
            raise PermissionError("denied")
        return sha256_hasher(path)

    assert find_duplicates([a, b], failing_hasher) == []


def test_file_changed_during_full_hash_is_not_declared_duplicate(tmp_path):
    a = make_file(tmp_path / "a.bin", b"same")
    b = make_file(tmp_path / "b.bin", b"same")

    def mutating_hasher(path: Path) -> str:
        digest = sha256_hasher(path)
        if path == a.path:
            path.write_bytes(b"diff")
        return digest

    assert find_duplicates([a, b], mutating_hasher) == []
