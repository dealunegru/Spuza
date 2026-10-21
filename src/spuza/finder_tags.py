"""Finder tags mirror for copies on a Mac (task spp-model, dec-054): best effort, never fails a copy.

A pill becomes a Finder tag named like the pill, in the nearest of Finder's seven colours. The tags live in the
extended attribute `com.apple.metadata:_kMDItemUserTags`, a binary plist array of "Name\\nN" strings (N = colour 0..7).
Only on macOS (sys.platform == "darwin"); elsewhere write_tags returns False. The libc layer is ctypes (no extra
wheel) behind the module-level `_xattr` object so tests can swap in a fake.
"""

from __future__ import annotations

import ctypes
import os
import plistlib
import sys
from collections.abc import Sequence

from spuza import pills

FINDER_COLOURS = ("none", "gray", "green", "purple", "blue", "yellow", "red", "orange")
TAGS_ATTR = "com.apple.metadata:_kMDItemUserTags"
INFO_ATTR = "com.apple.FinderInfo"
_ENOATTR = 93  # macOS errno for "no such attribute"


def nearest_finder(hex_: str) -> int:
    """Finder colour index 0..7 for a hex colour (a swatch colour maps to its own index)."""
    return pills.nearest_swatch(hex_).finder


def _finder_of(p) -> int:
    """Finder colour for a pill-like object: its `.hex` (nearest swatch) when it has one (an XMP tag), else the Finder
    index of the swatch named by its `.color` (a `pills.Pill`; an unknown name is 0)."""
    hex_ = getattr(p, "hex", None)
    if hex_ is not None:
        return nearest_finder(hex_)
    return pills.swatch(str(p.color)).finder


def payload(items: Sequence[tuple[str, int]]) -> bytes:
    """The binary-plist bytes for `_kMDItemUserTags`: an array of "Name\\nN" strings."""
    return plistlib.dumps([f"{name}\n{int(colour)}" for name, colour in items], fmt=plistlib.FMT_BINARY)


def parse(data: bytes) -> list[tuple[str, int]]:
    """(name, colour) pairs from tag bytes; tolerant: junk gives [], a bare "Name" is colour 0, a bad colour is 0."""
    try:
        arr = plistlib.loads(data)
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    out: list[tuple[str, int]] = []
    for s in arr:
        if not isinstance(s, str) or not s:
            continue
        name, _, col = s.partition("\n")
        colour = int(col) if col.isdigit() and int(col) < len(FINDER_COLOURS) else 0
        if name:
            out.append((name, colour))
    return out


class _Libc:
    """getxattr/setxattr through ctypes (macOS signatures take position and options)."""

    def __init__(self):
        self._lib = ctypes.CDLL(None, use_errno=True)
        self._lib.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.c_uint32, ctypes.c_int]
        self._lib.getxattr.restype = ctypes.c_ssize_t
        self._lib.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.c_uint32, ctypes.c_int]
        self._lib.setxattr.restype = ctypes.c_int

    def get(self, path, name: str) -> bytes | None:
        p, n = os.fsencode(path), name.encode()
        size = self._lib.getxattr(p, n, None, 0, 0, 0)
        if size < 0:
            err = ctypes.get_errno()
            if err == _ENOATTR:
                return None
            raise OSError(err, os.strerror(err))
        buf = ctypes.create_string_buffer(size)
        got = self._lib.getxattr(p, n, buf, size, 0, 0)
        if got < 0:
            err = ctypes.get_errno()
            if err == _ENOATTR:
                return None
            raise OSError(err, os.strerror(err))
        return buf.raw[:got]

    def set(self, path, name: str, value: bytes) -> None:
        buf = ctypes.create_string_buffer(value, len(value))
        if self._lib.setxattr(os.fsencode(path), name.encode(), buf, len(value), 0, 0) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))


_xattr = None  # created on first use on macOS; tests assign a fake with get(path, name) and set(path, name, value)


def write_tags(path, pills_) -> bool:
    """Mirror `pills_` (objects with .name/.hex) as Finder tags on `path`. True when written; never raises."""
    global _xattr
    try:
        if sys.platform != "darwin":
            return False
        items = [(str(p.name), _finder_of(p)) for p in pills_]
        if not items:
            return True
        if _xattr is None:
            _xattr = _Libc()
        have = _xattr.get(path, TAGS_ATTR)
        mine = {n.casefold() for n, _ in items}
        merged = [(n, c) for n, c in (parse(have) if have else []) if n.casefold() not in mine] + items
        if _xattr.get(path, INFO_ATTR) is None:
            _xattr.set(path, INFO_ATTR, bytes(32))
        _xattr.set(path, TAGS_ATTR, payload(merged))
        return True
    except Exception:
        return False
