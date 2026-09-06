"""Windows directory handles that reject reparses and prevent ancestor renames.

Handles omit FILE_SHARE_DELETE and stay open for the entire path operation.
See Microsoft's CreateFileW sharing and FILE_FLAG_OPEN_REPARSE_POINT contracts.
"""

from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path


class FileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("created", wintypes.FILETIME),
        ("accessed", wintypes.FILETIME),
        ("modified", wintypes.FILETIME),
        ("volume", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


def _api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    api.GetFileInformationByHandle.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _open(api, path, *, directory):
    # Read attributes for directories; read data for regular files. No writes.
    access = 0x80 if directory else 0x80000000
    share = 0x1 | 0x2 if directory else 0x1
    handle = api.CreateFileW(str(path), access, share, None, 3,
                             0x02000000 | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = FileInformation()
        if not api.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise OSError("reparse point or unexpected file type: {}".format(path))
    except BaseException:
        api.CloseHandle(handle)
        raise
    return handle


@contextmanager
def locked_directory(path: Path):
    absolute = Path(os.path.abspath(path))
    if str(absolute).startswith("\\\\"):
        raise OSError("network and device paths are excluded")
    api = _api()
    handles = []
    try:
        for component in list(reversed(absolute.parents)) + [absolute]:
            handles.append(_open(api, component, directory=True))
        yield
    finally:
        for handle in reversed(handles):
            api.CloseHandle(handle)


@contextmanager
def regular_descriptor(path: Path):
    import msvcrt

    with locked_directory(path.parent):
        api = _api()
        handle = _open(api, path, directory=False)
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            api.CloseHandle(handle)
            raise
        try:
            yield descriptor
        finally:
            os.close(descriptor)
