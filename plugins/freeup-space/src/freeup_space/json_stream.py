"""Canonical JSON without constructing duplicate inventories in memory."""
import json
import errno
import gzip
import os
import stat
import uuid
import zlib
from itertools import chain
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path

from .models import _json_safe, ScanRun, CleanupPlan


def canonical_chunks(value):
    if is_dataclass(value) and not isinstance(value, (type, ScanRun, CleanupPlan)):
        # Encode one bounded record in C, rather than yielding every JSON token.
        yield json.dumps(_json_safe(value), ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(",", ":"))
        return
    if is_dataclass(value) and not isinstance(value, type):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, Mapping):
        yield "{"
        for index, key in enumerate(sorted(value, key=str)):
            if index:
                yield ","
            yield json.dumps(str(key), ensure_ascii=False)
            yield ":"
            yield from canonical_chunks(value[key])
        yield "}"
    elif isinstance(value, (list, tuple, set, frozenset)):
        yield "["
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        for index, item in enumerate(items):
            if index:
                yield ","
            yield from canonical_chunks(item)
        yield "]"
    else:
        yield json.dumps(_json_safe(value), ensure_ascii=False, allow_nan=False,
                         separators=(",", ":"))


COMPRESSION_THRESHOLD = 1024 * 1024


def artifact_path(path):
    """Resolve new compressed artifacts while accepting existing plain files."""
    path = Path(path)
    compressed = path.with_name(path.name + ".gz")
    return compressed if compressed.exists() else path


def open_artifact(path):
    path = artifact_path(path)
    if getattr(path.stat(), "st_flags", 0) & getattr(stat, "SF_DATALESS", 0x40000000):
        raise ValueError("Scan data is offloaded: {}. Download it locally before resuming.".format(path))
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open(encoding="utf-8")


def load_json(path):
    try:
        with open_artifact(path) as stream:
            return json.load(stream)
    except FileNotFoundError as error:
        raise ValueError("missing run artifact: {}".format(path)) from error
    except (json.JSONDecodeError, gzip.BadGzipFile, EOFError, zlib.error, UnicodeError) as error:
        raise ValueError("corrupt run artifact: {}".format(path)) from error


def _byte_blocks(chunks):
    """Batch tiny JSON tokens; never stage the entire document in memory."""
    buffer = bytearray()
    for chunk in chunks:
        data = chunk.encode("utf-8")
        for offset in range(0, len(data), 65536):
            buffer.extend(data[offset:offset + 65536])
            if len(buffer) >= 65536:
                yield bytes(buffer)
                buffer.clear()
    if buffer:
        yield bytes(buffer)


def atomic_text(path, chunks, progress=None):
    """Stream large artifacts directly to gzip, with an atomic commit.

    Only the first MiB is buffered to keep small artifacts human-readable. Once
    compressed, a path keeps that format so updates have a single commit point.
    A failed write leaves the previous artifact intact and discards only its own
    temporary file. No uncompressed multi-gigabyte staging file is created.
    """
    path = Path(path)
    if path.suffix == ".gz":
        path = path.with_suffix("")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    def measured_blocks():
        for block in _byte_blocks(chunks):
            if progress:
                progress.advance(len(block))
            yield block
    blocks = iter(measured_blocks())
    prefix = bytearray()
    compressed = path.with_name(path.name + ".gz")
    use_gzip = compressed.exists()
    try:
        if not use_gzip:
            for block in blocks:
                prefix.extend(block)
                if len(prefix) > COMPRESSION_THRESHOLD:
                    use_gzip = True
                    break
        with temporary.open("xb") as raw:
            if use_gzip:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                   compresslevel=3, mtime=0) as stream:
                    stream.writelines(chain((bytes(prefix),), blocks))
            else:
                raw.write(prefix)
        destination = compressed if use_gzip else path
        os.replace(temporary, destination)
        if use_gzip:
            # Only a superseded plugin-generated artifact, after a safe commit.
            path.unlink(missing_ok=True)
        if progress:
            progress.finish()
        return destination
    except OSError as error:
        if error.errno in {errno.ENOSPC, errno.EDQUOT}:
            raise OSError(error.errno,
                          "Not enough space to save scan data in {}. "
                          "Free some space and resume the saved scan if available; "
                          "the previous saved artifact was preserved.".format(path.parent)) from error
        raise
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path, value, progress=None):
    return atomic_text(path, chain(canonical_chunks(value), ("\n",)), progress)
