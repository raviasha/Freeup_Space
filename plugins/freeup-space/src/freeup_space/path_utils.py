"""Lexical path normalization, including policy tests on the opposite host."""

import ntpath
import os
import posixpath


def normalize_path(value, platform):
    if platform == "windows":
        return ntpath.normcase(ntpath.normpath(value.replace("/", "\\")))
    if os.name == "nt":
        # WindowsPath renders even abstract /System fixtures with backslashes.
        # Normalize those without changing valid backslashes in real Mac filenames.
        value = value.replace("\\", "/")
        if not posixpath.isabs(value) and not ntpath.splitdrive(value)[0]:
            value = posixpath.join(os.getcwd().replace("\\", "/"), value)
        return posixpath.normpath(value)
    return posixpath.abspath(posixpath.normpath(value))
