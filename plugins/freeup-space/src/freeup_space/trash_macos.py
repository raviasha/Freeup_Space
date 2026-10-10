"""Native recoverable deletion helpers for macOS."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


# Foundation returns errors to us without opening Finder's per-file dialogs.
# Arguments are passed separately, never interpolated into executable source.
NATIVE_SCRIPT = r'''
ObjC.import('Foundation');
function run(argv) {
    const fm = $.NSFileManager.defaultManager;
    const keys = $([$.NSURLIsUbiquitousItemKey, $.NSURLIsDirectoryKey,
        $.NSURLIsSymbolicLinkKey, $.NSURLFileSizeKey,
        $.NSURLUbiquitousItemDownloadingStatusKey,
        $.NSURLUbiquitousItemDownloadingErrorKey]);
    const deadline = Number($.NSProcessInfo.processInfo.systemUptime) + Number(argv[2]);
    function description(error, fallback) {
        return ObjC.unwrap(error) ? String(ObjC.unwrap(error.localizedDescription)) : fallback;
    }
    function values(url) {
        const result = url.resourceValuesForKeysError(keys, null);
        if (!ObjC.unwrap(result)) throw Error('Cannot inspect item; it may be missing or inaccessible');
        return result;
    }
    function get(v, key) { return ObjC.unwrap(v.objectForKey(key)); }
    function ready(v) {
        if (!get(v, $.NSURLIsUbiquitousItemKey)) return true;
        const status = get(v, $.NSURLUbiquitousItemDownloadingStatusKey);
        return status === ObjC.unwrap($.NSURLUbiquitousItemDownloadingStatusCurrent) ||
            status === ObjC.unwrap($.NSURLUbiquitousItemDownloadingStatusDownloaded);
    }
    function checkTime() {
        if (Number($.NSProcessInfo.processInfo.systemUptime) >= deadline)
            throw Error('iCloud download timed out; item was not moved to Trash');
    }
    function prepare(path) {
        checkTime();
        let url = $.NSURL.fileURLWithPath(path);
        let v = values(url);
        if (get(v, $.NSURLIsSymbolicLinkKey)) throw Error('Linked items cannot be prepared');
        if (!ready(v)) {
            const fs = fm.attributesOfFileSystemForPathError(path, null);
            if (!ObjC.unwrap(fs)) throw Error('Cannot check free disk space');
            const size = Number(get(v, $.NSURLFileSizeKey));
            const free = Number(get(fs, $.NSFileSystemFreeSize));
            if (!Number.isFinite(size) || !Number.isFinite(free) || size + 16777216 > free)
                throw Error('Not enough local space to download iCloud item; item was not moved to Trash');
            if (!fm.startDownloadingUbiquitousItemAtURLError(url, null))
                throw Error('Could not start iCloud download; check network and iCloud availability');
            while (true) {
                checkTime();
                // A fresh URL avoids cached resource values while downloading.
                url = $.NSURL.fileURLWithPath(path);
                v = values(url);
                if (get(v, $.NSURLIsSymbolicLinkKey)) throw Error('Item became a link during download');
                if (ready(v)) break;
                const downloadError = v.objectForKey($.NSURLUbiquitousItemDownloadingErrorKey);
                if (ObjC.unwrap(downloadError)) throw Error(description(downloadError, 'iCloud download failed'));
                $.NSThread.sleepForTimeInterval(0.25);
            }
        }
        if (get(v, $.NSURLIsDirectoryKey)) {
            const children = fm.contentsOfDirectoryAtURLIncludingPropertiesForKeysOptionsError(url, keys, 0, null);
            if (!ObjC.unwrap(children)) throw Error('Cannot inspect folder contents');
            for (let i = 0; i < Number(children.count); i++)
                prepare(String(ObjC.unwrap(children.objectAtIndex(i).path)));
        }
    }
    try {
        if (argv[0] === 'prepare') {
            prepare(argv[1]);
            return JSON.stringify({ok:true});
        }
        if (argv[0] !== 'trash') throw Error('Unknown native operation');
        // JXA's NSError/NSURL out-parameter bridge can crash on recent macOS.
        // These outputs are optional; use the BOOL result and no GUI fallback.
        if (!fm.trashItemAtURLResultingItemURLError($.NSURL.fileURLWithPath(argv[1]), null, null))
            throw Error('macOS could not move item to Trash; check permissions and iCloud availability');
        return JSON.stringify({ok:true});
    } catch (error) {
        return JSON.stringify({ok:false, error:String(error.message || error)});
    }
}
'''


def _native_operation(path: Path, platform: str, action: str, seconds: int) -> dict:

    if platform != "macos":
        raise ValueError("macOS trash adapter received {!r}".format(platform))
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", NATIVE_SCRIPT,
             action, str(path), str(seconds)],
            capture_output=True, text=True, timeout=seconds + 5, check=False,
        )
    except subprocess.TimeoutExpired as error:
        detail = ("iCloud preparation timed out; item was not moved to Trash"
                  if action == "prepare" else
                  "macOS Trash operation timed out; check the item and Trash before retrying")
        raise RuntimeError(detail) from error
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "macOS {} failed (exit {})".format(action, result.returncode))
    try:
        response = json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise RuntimeError("macOS returned an invalid {} result".format(action)) from error
    if not isinstance(response, dict) or response.get("ok") is not True:
        detail = response.get("error") if isinstance(response, dict) else None
        raise RuntimeError(detail or "macOS {} failed".format(action))
    return response


def prepare_for_trash(path: Path, platform: str) -> None:
    """Download missing iCloud content within a two-minute per-candidate budget.

    Call only after approval, then revalidate the original snapshot before moving.
    """
    _native_operation(path, platform, "prepare", 120)


def move_to_trash(path: Path, platform: str) -> bool:
    """Use the native recoverable Trash API without Finder dialogs."""
    _native_operation(path, platform, "trash", 30)
    return True


def empty_trash() -> None:
    """Intentionally unused in this project."""

    raise RuntimeError("empty_trash must never be called")
