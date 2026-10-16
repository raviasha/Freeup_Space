"""Human cleanup reasons over saved evidence; recommendations never change eligibility."""
import hashlib
import json
import math
import ntpath
import posixpath
import re
import unicodedata
from datetime import datetime, timezone

from .old_files import _months_before
from .similar_groups import FAMILIES


PERSONAL_FAMILIES = {"Videos", "Audio", "Images", "Documents", "Presentations", "Spreadsheets"}
VERSION = re.compile(r"^(.*?)[\s._-]+(?:v|ver|version|rev|revision)[\s._-]*(\d+(?:\.\d+)*)$", re.I)
LARGE_BYTES = 100 * 1024 * 1024


def version_key(path, platform="macos"):
    """Only explicit revisions in the same folder and format form a version family."""
    path_module = ntpath if platform == "windows" else posixpath
    name = unicodedata.normalize("NFKC", path_module.basename(str(path))).casefold()
    stem, ext = ntpath.splitext(name)
    if FAMILIES.get(ext) not in PERSONAL_FAMILIES:
        return None
    match = VERSION.fullmatch(stem)
    if not match or not any(c.isalpha() for c in match[1]):
        return None
    # Bound integer parsing for filenames from untrusted scans.
    if len(match[2]) > 60:
        return None
    revision = tuple(int(n) for n in match[2].split("."))
    while len(revision) > 1 and revision[-1] == 0:
        revision = revision[:-1]
    parent = path_module.normpath(path_module.dirname(str(path)))
    if platform == "windows":
        parent = parent.casefold()
    base = re.sub(r"[\s._-]+", " ", match[1]).strip()
    return (parent, base, ext), revision


def timestamp(value, reference=None):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    if value <= 0 or reference is not None and value > reference or value > 253402300799 or not math.isfinite(value):
        return None
    return float(value)


def scan_time(plan):
    try:
        value = datetime.fromisoformat(plan["created_at"].replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def build_recommendations(plan, actionable):
    candidates = plan.get("candidates", [])
    now = scan_time(plan)
    reference = now.timestamp() if now else None
    by_id = {c["candidate_id"]: c for c in candidates}
    by_path = {c["path"]: c for c in candidates}
    members, proposed, descriptions, details, dependencies = {}, {}, {}, {}, {}
    reserved = set()

    def add(key, title, description, items, recommended=(), **extra):
        members[key] = list(items)
        proposed[key] = list(recommended)
        descriptions[key] = dict(category=key, title=title, description=description, **extra)

    personal, versions, copies, clutter, downloads = [], {}, [], [], []
    for c in candidates:
        identifier = c["candidate_id"]
        categories = set(c.get("evidence", {}).get("categories", ())) | {c["category"]}
        modified = timestamp(c.get("snapshot", {}).get("mtime"), reference)
        ext = ntpath.splitext(c["path"])[1].casefold()
        is_personal = c.get("file_kind", "regular") == "regular" and (
            "personal-data" in categories or FAMILIES.get(ext) in PERSONAL_FAMILIES)
        history = c.get("evidence", {}).get("opening_history", {})
        opened = timestamp(history.get("last_opened_at"), reference) if (
            reference and history.get("source") == "macos-launchservices") else None
        detail = dict(is_personal=is_personal, modified_at=modified, last_opened_at=opened,
                      opening_history_label="Last recorded opening" if opened else "Opening history unavailable")
        details[identifier] = detail
        if is_personal:
            personal.append(c)
            key = version_key(c["path"], plan.get("platform", "macos"))
            if key:
                versions.setdefault(key[0], []).append((key[1], c))
        retained = c.get("evidence", {}).get("retained_path")
        if retained and c.get("snapshot", {}).get("digest"):
            copies.append(c)
            detail["recommendation_reason"] = "Verified extra copy; keep " + retained
            keeper = by_path.get(retained)
            if keeper:
                reserved.add(keeper["candidate_id"])
                dependencies.setdefault(identifier, set()).add(keeper["candidate_id"])
        if categories & {"cache", "log", "temporary"}:
            clutter.append(c)
        if categories & {"download", "installer", "archive"} and reference and modified and modified <= reference - 90 * 86400:
            downloads.append(c)

    personal_ids = {c["candidate_id"] for c in personal}
    version_cards, version_members, version_old = [], [], []
    for key, entries in sorted(versions.items()):
        if len(entries) < 2:
            continue
        highest = max(revision for revision, _ in entries)
        keepers = [c for revision, c in entries if revision == highest]
        older = [c for revision, c in entries if revision < highest]
        if not older:
            continue
        identifier = "version:" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]
        note = ""
        if len(keepers) != 1:
            note = "Several files claim the latest version. Review them before choosing what to keep."
        elif any(details[c["candidate_id"]]["modified_at"] is None for _, c in entries):
            note = "Modification history is unavailable. Review the version order."
        elif any(details[c["candidate_id"]]["modified_at"] > details[keepers[0]["candidate_id"]]["modified_at"] for c in older):
            note = "An earlier version was modified more recently. Review which version to keep."
        keep_ids = {c["candidate_id"] for c in keepers}
        reserved.update(keep_ids)
        for revision, c in entries:
            detail = details[c["candidate_id"]]
            detail.update(version_role="keep" if c["candidate_id"] in keep_ids else "older", version=".".join(map(str, revision)),
                          keep_paths=[k["path"] for k in keepers], version_note=note)
            if c["candidate_id"] not in keep_ids and not note:
                detail["recommendation_reason"] = "Likely superseded version; review for unique content."
                dependencies.setdefault(c["candidate_id"], set()).update(keep_ids)
        items = [c for _, c in sorted(entries, key=lambda pair: (pair[0], pair[1]["path"]), reverse=True)]
        recommended = [] if note else [c["candidate_id"] for c in older]
        add(identifier, key[1] + key[2], "Explicit revisions in the same folder and format. Keep only the latest version.",
            items, recommended, keep_paths=[c["path"] for c in keepers], note=note)
        version_cards.append(identifier)
        version_members.extend(items)
        version_old.extend(recommended)

    add("review:clutter", "Disposable clutter", "Known caches, logs and temporary files. Recommendations require low-risk evidence and no modification for at least 7 days.", clutter,
        [c["candidate_id"] for c in clutter if reference and c.get("file_kind", "regular") == "regular" and c["risk"] == "low"
         and details[c["candidate_id"]]["modified_at"] and details[c["candidate_id"]]["modified_at"] <= reference - 7 * 86400])
    add("review:extra-copies", "Extra copies", "Verified identical contents with a retained copy. Review the locations before removing extras.", copies, [c["candidate_id"] for c in copies])
    add("review:older-versions", "Older versions", "Keep only the latest explicit revision. Earlier drafts may contain unique content and need review.", version_members, version_old)
    add("review:large-personal", "Large personal files", "Personal files of at least 100 MiB, largest first. Size alone is not a cleanup recommendation.", [c for c in personal if c["reclaimable_bytes"] >= LARGE_BYTES])
    add("review:old-downloads", "Old downloads and installers", "Downloads, archives and installers not modified for at least 90 days. Personal documents and media need individual review.", downloads,
        [c["candidate_id"] for c in downloads if c["candidate_id"] not in personal_ids and c["risk"] in {"low", "medium"}])
    for months in (6, 12, 24):
        cutoff = _months_before(now, months).timestamp() if now else None
        not_opened, not_modified = [], []
        for c in personal:
            d = details[c["candidate_id"]]
            if cutoff and d["modified_at"] and d["modified_at"] <= cutoff:
                if d["last_opened_at"] and d["last_opened_at"] <= cutoff:
                    not_opened.append(c)
                elif d["last_opened_at"] is None:
                    not_modified.append(c)
        add("review:not-opened:%d" % months, "Not opened recently", "No recorded opening or modification for at least %d months. Recorded opening history may be incomplete; age alone is not a recommendation." % months, not_opened, months=months)
        add("review:not-modified:%d" % months, "Not modified recently", "Not modified for at least %d months; opening history is unavailable. This does not prove the files were unused." % months, not_modified, months=months)

    # A file promised as Keep by any recommendation must never be recommended elsewhere.
    recommended = {key: [i for i in ids if i not in reserved and actionable(by_id[i])] for key, ids in proposed.items()}
    all_recommended = set().union(*(set(ids) for ids in recommended.values()))
    for key in members:
        if key.startswith(("review:not-", "review:large-")):
            recommended[key] = [c["candidate_id"] for c in members[key] if c["candidate_id"] in all_recommended]
        members[key].sort(key=lambda c: (-c["reclaimable_bytes"], c["path"], c["candidate_id"]))
        descriptions[key].update(count=len(members[key]), recommended_count=len(recommended[key]),
            recommended_bytes=sum(by_id[i]["reclaimable_bytes"] for i in recommended[key]),
            review_bytes=sum(c["reclaimable_bytes"] for c in members[key] if actionable(c)))
    cards = [descriptions[key] for key in ("review:clutter", "review:extra-copies", "review:older-versions",
             "review:not-opened:12", "review:large-personal", "review:old-downloads", "review:not-modified:12")]
    version_cards.sort(key=lambda key: (-descriptions[key]["recommended_bytes"], descriptions[key]["title"], key))
    return dict(cards=cards, descriptions=descriptions, members=members, recommended=recommended,
                details=details, dependencies=dependencies, version_cards=version_cards,
                unknown_count=sum(details[c["candidate_id"]]["last_opened_at"] is None for c in personal))
