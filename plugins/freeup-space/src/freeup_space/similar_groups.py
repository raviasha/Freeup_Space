"""Deterministic filename families over an immutable plan, without filesystem I/O."""
import hashlib
import json
import ntpath
import re
import unicodedata


FORMATS = {
    "Videos": "mp4 mov mkv m4v avi webm mpg mpeg wmv flv 3gp mts m2ts",
    "Audio": "mp3 m4a aac wav flac ogg opus aiff aif wma",
    "Images": "jpg jpeg png heic heif webp gif tiff tif bmp avif dng raw cr2 nef arw",
    "Documents": "pdf doc docx odt rtf txt pages",
    "Presentations": "ppt pptx odp key",
    "Spreadsheets": "xls xlsx ods csv tsv numbers",
    "Archives": "zip 7z rar tar tar.gz tar.bz2 tar.xz tgz gz bz2 xz",
}
FAMILIES = {"." + ext: family for family, formats in FORMATS.items() for ext in formats.split()}
COPY_SUFFIX = re.compile(r"(?:\s*\(\d+\)|[\s._-]+copy(?:[\s._-]*\d+)?|[\s._-]+\(copy(?:\s+\d+)?\))$")


def name_family(candidate):
    if candidate.get("file_kind", "regular") not in {None, "regular"}:
        return None
    name = unicodedata.normalize("NFKC", ntpath.basename(candidate["path"])).casefold()
    stem, ext = ntpath.splitext(name)
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if name.endswith(suffix):
            stem, ext = name[:-len(suffix)], suffix
            break
    if not ext:
        return None
    while COPY_SUFFIX.search(stem):
        stem = COPY_SUFFIX.sub("", stem)
    # Numeric-only names are too ambiguous to group just by extension.
    if not any(c.isalpha() for c in stem):
        return None
    pattern = re.sub(r"[\s._-]+", " ", re.sub(r"\d+", "#", stem)).strip()
    return FAMILIES.get(ext, ext.upper() + " files"), pattern, ext


def build_groups(candidates, actionable):
    buckets = {}
    for candidate in candidates:
        family = name_family(candidate)
        if family is None:
            continue
        kind, pattern, ext = family
        bucket = buckets.setdefault((kind, pattern), {"members": [], "extensions": set()})
        bucket["members"].append(candidate)
        bucket["extensions"].add(ext)
    groups, members, by_candidate = {}, {}, {}
    for (kind, pattern), bucket in buckets.items():
        items = bucket["members"]
        if len(items) < 2:
            continue
        key = json.dumps([kind, pattern], ensure_ascii=False).encode("utf-8")
        identifier = "similar-" + hashlib.sha256(key).hexdigest()[:24]
        eligible = [c for c in items if actionable(c)]
        groups[identifier] = dict(group_id=identifier, label=kind + " · " + pattern, pattern=pattern,
            count=len(items), actionable_count=len(eligible), bytes=sum(c["reclaimable_bytes"] for c in eligible),
            extensions=sorted(bucket["extensions"]))
        members[identifier] = items
        for c in items:
            by_candidate[c["candidate_id"]] = identifier
    ordered = sorted(groups.values(), key=lambda g: (-g["bytes"], g["label"], g["group_id"]))
    return dict(groups=groups, members=members, by_candidate=by_candidate, ordered=ordered)
