"""Capture order straight from the camera's metadata: stdlib only, bounded, never raises.

File mtimes lie on real cards (a Ricoh GR III burst copied off a card came out 147, 146, 149,
148, 150). The camera knows better: ``DateTimeOriginal`` (plus ``SubSecTimeOriginal`` when it
writes one) and a running shot counter, Ricoh ``ShutterCount`` or Fuji ``ImageCount`` /
``SequenceNumber`` (or Nikon ``ShutterCount``), which separates shots taken inside one second.

``capture_key(path)`` gives a :class:`CaptureKey` or ``None``; ``sort_key(key, name)`` turns that
into a tuple a caller can sort on. Call it from a worker thread, not the UI thread: it opens the
file and does a few small reads (a fraction of a millisecond to a millisecond on a local disk).

Containers read: JPEG (APP1 Exif), TIFF-based files (DNG, ARW, NEF, PEF, ORF ... anything with a
TIFF header and an Exif IFD) and RAF (the embedded JPEG's Exif). HEIF/HEIC and CR3 are not read
(``None``).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

MAX_IFDS = 8  # IFDs opened per file (IFD0, Exif, a MakerNote, ...): no chain is ever followed
MAX_ENTRIES = 4096  # per IFD
MAX_READS = 64  # file reads per call
MAX_READ = 1 << 20  # bytes in any one read
MAX_SEGMENTS = 64  # JPEG segments walked before giving up
MAX_VALUE = 256  # bytes of any one tag value we decode

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
_DATE = re.compile(rb"(\d{4}):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)")
# 42 = TIFF, 0x4F52/0x5352 = Olympus ORF, 0x55 = Panasonic RW2: all the same IFD layout
_TIFF_MAGIC = (42, 0x4F52, 0x5352, 0x55)


@dataclass(frozen=True, order=True)
class CaptureKey:
    """What the camera says about when a picture was taken, ordered field by field.

    ``taken``   ``YYYY-MM-DDTHH:MM:SS`` in the camera's local clock (no time zone applied).
    ``subsec``  fraction digits of the second with trailing zeros removed (``""`` = none/zero),
                so plain string comparison orders them as fractions ("05" < "25" < "3").
    ``counter`` Ricoh/Nikon ShutterCount, Fuji ImageCount (else SequenceNumber); -1 = none, which
                sorts before any real counter at the same instant.
    """

    taken: str
    subsec: str = ""
    counter: int = -1


def sort_key(key: CaptureKey | None, name: str) -> tuple:
    """Order pictures: capture key, then file name; pictures with no key come after all keyed ones.

    ``(0, taken, subsec, counter, name)`` for a keyed picture, ``(1, "", "", 0, name)`` for one
    without; the tuples always compare (same shape and types). A caller that wants unkeyed files
    ordered by mtime builds its own tuple, e.g. ``(1, "", "", mtime_ns, name)``.
    """
    if key is None:
        return (1, "", "", 0, name)
    return (0, key.taken, key.subsec, key.counter, name)


class _Bail(Exception):
    """A read or budget limit was hit: give up on this file."""


class _Src:
    """Bounds-checked, read-budgeted random access to an open file."""

    def __init__(self, f, size: int):
        self.f, self.size, self.reads, self.ifds = f, size, 0, 0

    def at(self, off: int, n: int) -> bytes:
        if off < 0 or n < 0 or n > MAX_READ or off + n > self.size:
            return b""
        self.reads += 1
        if self.reads > MAX_READS:
            raise _Bail
        self.f.seek(off)
        return self.f.read(n)


def _entries(src: _Src, base: int, bo: str, off: int) -> dict:
    """{tag: (type, count, absolute value offset)} for the IFD at base+off ({} when unreadable)."""
    src.ifds += 1
    if src.ifds > MAX_IFDS:
        raise _Bail
    raw = src.at(base + off, 2)
    if len(raw) != 2:
        return {}
    n = struct.unpack(bo + "H", raw)[0]
    if n == 0 or n > MAX_ENTRIES:
        return {}
    blob = src.at(base + off + 2, 12 * n)
    if len(blob) != 12 * n:
        return {}
    out = {}
    for i in range(n):
        tag, typ, cnt = struct.unpack_from(bo + "HHI", blob, 12 * i)
        size = _TYPE_SIZE.get(typ, 1) * cnt
        if size <= 4:
            pos = base + off + 2 + 12 * i + 8
        else:
            pos = base + struct.unpack_from(bo + "I", blob, 12 * i + 8)[0]
        out[tag] = (typ, cnt, pos)
    return out


def _raw(src: _Src, ent, n: int | None = None) -> bytes:
    typ, cnt, pos = ent
    size = _TYPE_SIZE.get(typ, 1) * cnt
    return src.at(pos, min(size if n is None else n, MAX_VALUE))


def _uint(src: _Src, ent, bo: str) -> int | None:
    if not ent or ent[0] not in (3, 4) or ent[1] < 1:
        return None
    n = 2 if ent[0] == 3 else 4
    raw = src.at(ent[2], n)
    return struct.unpack(bo + ("H" if n == 2 else "I"), raw)[0] if len(raw) == n else None


def _ascii(src: _Src, ent) -> bytes:
    if not ent or ent[0] != 2:
        return b""
    return _raw(src, ent).split(b"\0", 1)[0].strip()


# ---- MakerNotes: each returns the shot counter or None -----------------------------------------

def _fuji(src: _Src, start: int) -> int | None:
    hdr = src.at(start, 12)
    if not hdr.startswith(b"FUJIFILM"):
        return None
    ents = _entries(src, start, "<", struct.unpack("<I", hdr[8:12])[0])
    count = _uint(src, ents.get(0x1438), "<")  # ImageCount, exiftool masks it with 0x7fff
    if count is not None:
        return count & 0x7FFF
    return _uint(src, ents.get(0x1101), "<")  # SequenceNumber


def _ricoh(src: _Src, start: int) -> int | None:
    hdr = src.at(start, 8)
    if not hdr.startswith(b"RICOH\0") or hdr[6:8] not in (b"II", b"MM"):
        return None
    bo = "<" if hdr[6:8] == b"II" else ">"
    ents = _entries(src, start, bo, 8)
    date, time_, sc = ents.get(0x0006), ents.get(0x0007), ents.get(0x005D)
    if not (date and time_ and sc) or date[1] != 4 or time_[1] < 3 or sc[1] != 4:
        return None
    d, t, c = src.at(date[2], 4), src.at(time_[2], 3), src.at(sc[2], 4)
    if len(d) != 4 or len(t) != 3 or len(c) != 4:
        return None
    # ShutterCount is stored XORed with the date and the inverted time (exiftool CryptShutterCount)
    return (struct.unpack(">I", c)[0] ^ struct.unpack(">I", d)[0]
            ^ (0xFFFFFFFF - struct.unpack(">I", t + b"\0")[0]))


def _nikon(src: _Src, start: int) -> int | None:
    if src.at(start, 6) != b"Nikon\0":
        return None
    base = start + 10
    hdr = src.at(base, 8)
    bo = {b"II": "<", b"MM": ">"}.get(hdr[:2])
    if bo is None or struct.unpack(bo + "H", hdr[2:4])[0] != 42:
        return None
    ents = _entries(src, base, bo, struct.unpack(bo + "I", hdr[4:8])[0])
    return _uint(src, ents.get(0x00A7), bo)  # ShutterCount


def _makernote(src: _Src, start: int) -> int | None:
    head = src.at(start, 12)
    if head.startswith(b"FUJIFILM"):
        return _fuji(src, start)
    if head.startswith(b"RICOH\0"):
        return _ricoh(src, start)
    if head.startswith(b"Nikon\0"):
        return _nikon(src, start)
    return None


# ---- TIFF structure ----------------------------------------------------------------------------

def _tiff(src: _Src, base: int) -> tuple[bytes, str, int] | None:
    """The CaptureKey parts (taken, subsec, counter) of the TIFF structure at `base`, or None."""
    hdr = src.at(base, 8)
    bo = {b"II": "<", b"MM": ">"}.get(hdr[:2])
    if bo is None or len(hdr) != 8 or struct.unpack(bo + "H", hdr[2:4])[0] not in _TIFF_MAGIC:
        return None
    ifd0_off = struct.unpack(bo + "I", hdr[4:8])[0]
    ifd0 = _entries(src, base, bo, ifd0_off)
    ptr = _uint(src, ifd0.get(0x8769), bo)
    if not ptr or ptr == ifd0_off:
        return None
    exif = _entries(src, base, bo, ptr)
    taken = _ascii(src, exif.get(0x9003))
    if not _DATE.fullmatch(taken):
        return None
    subsec = _ascii(src, exif.get(0x9291))
    counter = None
    mn = exif.get(0x927C)
    if mn and mn[1] >= 8:
        counter = _makernote(src, mn[2])
    if counter is None:
        priv = ifd0.get(0xC634)  # DNGPrivateData
        if priv and priv[1] >= 8:
            head = src.at(priv[2], 20)
            if head.startswith(b"RICOH\0"):  # GR III: the private data is the bare MakerNote
                counter = _makernote(src, priv[2])
            elif head.startswith(b"Adobe\0MakN"):  # Adobe wrapper: 20 bytes, then the MakerNote
                counter = _makernote(src, priv[2] + 20)
    return taken, subsec.decode("ascii", "replace"), counter


def _jpeg(src: _Src, start: int, end: int) -> tuple | None:
    pos = start + 2
    for _ in range(MAX_SEGMENTS):
        if pos + 4 > end:
            return None
        mk = src.at(pos, 4)
        if len(mk) < 4 or mk[0] != 0xFF or mk[1] in (0xDA, 0xD9):
            return None
        ln = struct.unpack(">H", mk[2:4])[0]
        if ln < 2:
            return None
        if mk[1] == 0xE1 and src.at(pos + 4, 6) == b"Exif\0\0":
            return _tiff(src, pos + 10)
        pos += 2 + ln
    return None


def _parts(f, size: int) -> tuple | None:
    src = _Src(f, size)
    head = src.at(0, min(size, 0x5C))
    if head.startswith(b"FUJIFILMCCD-RAW"):
        if len(head) < 0x5C:
            return None
        off, ln = struct.unpack(">II", head[0x54:0x5C])
        if ln < 4 or off + ln > size or src.at(off, 2) != b"\xff\xd8":
            return None
        return _jpeg(src, off, off + ln)
    if head[:2] == b"\xff\xd8":
        return _jpeg(src, 0, size)
    if head[:2] in (b"II", b"MM"):
        return _tiff(src, 0)
    return None


def capture_key(path) -> CaptureKey | None:
    """The camera's capture key for `path`, or None (unreadable, unsupported, no date, hostile).

    Never raises, reads at most MAX_READS small pieces of the file, no thread-shared state.
    """
    try:
        with open(Path(path), "rb") as f:
            f.seek(0, 2)
            parts = _parts(f, f.tell())
        if parts is None:
            return None
        taken, subsec, counter = parts
        y, mo, d, h, mi, s = _DATE.fullmatch(taken).groups()
        digits = subsec.strip()
        digits = digits[:9].rstrip("0") if digits.isdigit() else ""
        return CaptureKey(f"{y.decode()}-{mo.decode()}-{d.decode()}T{h.decode()}:{mi.decode()}:{s.decode()}",
                          digits, -1 if counter is None else counter)
    except Exception:  # noqa: BLE001 - the contract is "never raises"
        return None
