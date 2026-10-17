"""Rotation as the EXIF Orientation tag, written into the destination copies only. No Qt.

The user turns a picture in clockwise quarter steps; on copy Spuza changes only the angle (tag 0x0112) in the
copy it just made, never in the original on the card. A write is a same-length in-place patch of the tag's two
bytes, with one exception: a JPEG with no EXIF at all gets a minimal APP1 segment inserted after SOI.

Formats: JPEG (.jpg/.jpeg), DNG (TIFF IFD0 and the IFDs behind it), RAF (the EXIF of the JPEG embedded in the
file; the header offsets are not touched because nothing moves). Anything else is left alone and reported.

Every write is checked before the copy is published: the tag is re-read with this module's own reader, with
Pillow where it can open the file, and with exiftool when it is on PATH; and the copy must equal the source
bytes except for the patched bytes. Any doubt, and the copy is put back to the source's bytes.
"""

from __future__ import annotations

import functools
import io
import os
import shutil
import struct
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

CHUNK = 1024 * 1024
MAX_IFDS = 64  # cap against cyclic / hostile IFD chains
MAX_ENTRIES = 4096  # per IFD
MAX_SEGMENTS = 256  # JPEG marker segments walked while looking for the Exif one
MAX_SUBIFDS = 16  # offsets read from one SubIFDs tag
EXIFTOOL_SECONDS = 60.0  # the longest one exiftool check may take; slower counts as a doubt
TAG_ORIENTATION = 0x0112
JPEG_EXTS = frozenset({".jpg", ".jpeg"})
SUPPORTED_EXTS = JPEG_EXTS | {".dng", ".raf"}

# Orientation 1..8 as the 2x2 matrix that takes a stored pixel position (x right, y down) to its displayed one.
_MATRIX = {
    1: ((1, 0), (0, 1)),
    2: ((-1, 0), (0, 1)),  # mirrored left-right
    3: ((-1, 0), (0, -1)),  # 180
    4: ((1, 0), (0, -1)),  # mirrored top-bottom
    5: ((0, 1), (1, 0)),  # transposed
    6: ((0, -1), (1, 0)),  # 90 clockwise
    7: ((0, -1), (-1, 0)),  # transverse
    8: ((0, 1), (-1, 0)),  # 90 counter-clockwise
}
_CLOCKWISE = ((0, -1), (1, 0))  # one clockwise quarter turn of the displayed picture
_BY_MATRIX = {m: o for o, m in _MATRIX.items()}


def _mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(2)) for j in range(2)) for i in range(2))


def compose(orientation: int, turns: int) -> int:
    """The EXIF orientation (1..8) of a picture the camera stored as ``orientation`` after the user turns it
    ``turns`` clockwise quarter steps. A value outside 1..8 counts as 1 (no rotation, no mirror)."""
    m = _MATRIX.get(orientation if isinstance(orientation, int) and not isinstance(orientation, bool) else 1,
                    _MATRIX[1])
    for _ in range(int(turns) % 4):
        m = _mul(_CLOCKWISE, m)
    return _BY_MATRIX[m]


class _Doubt(Exception):
    """The file is not what the patch expects: write nothing."""


@dataclass(frozen=True)
class Patch:
    """What turns the source's bytes into the rotated copy's. ``edits`` are same-length (offset, old, new);
    ``insert`` is (offset, bytes) for the one case that adds a segment. Applying one never needs the source twice."""

    kind: str  # "jpeg" | "jpeg-noexif" | "dng" | "raf"
    before: int  # IFD0 orientation in the source (1 when it carries none)
    after: int  # IFD0 orientation in the rotated copy
    edits: tuple[tuple[int, bytes, bytes], ...] = ()
    insert: tuple[int, bytes] | None = None
    tags: tuple[int, ...] = ()  # every orientation value the copy must hold afterwards, sorted

    def segments(self, size: int):
        """The expected copy as ("src", start, end) ranges of the source and ("lit", bytes) literals."""
        out: list = []
        pos = 0
        for off, _old, new in sorted(self.edits):
            out += [("src", pos, off), ("lit", new)]
            pos = off + len(new)
        if self.insert is not None:
            out += [("src", pos, self.insert[0]), ("lit", self.insert[1])]
            pos = self.insert[0]
        out.append(("src", pos, size))
        return [s for s in out if not (s[0] == "src" and s[1] == s[2]) and not (s[0] == "lit" and not s[1])]


@dataclass
class Outcome:
    """What happened to one file. status: "none" (turns 0), "written", or "kept" (original orientation kept)."""

    status: str
    before: int = 0
    after: int = 0
    reason: str = ""
    checked_with: list[str] = field(default_factory=list)  # which readers confirmed the write


# ---- reading ---------------------------------------------------------------------------------------------

def _u16(bo, b):
    return struct.unpack(bo + "H", b)[0]


def _u32(bo, b):
    return struct.unpack(bo + "I", b)[0]


def _walk_tiff(f, base: int, limit: int):
    """Every orientation slot in the TIFF structure at ``base`` (absolute file positions, offsets inside it
    relative to ``base``, nothing read at or past ``limit``): IFD0, the IFDs chained after it, and SubIFDs.
    Returns [(absolute offset of the 2-byte value, value, byte order)], IFD0's first when it has one."""
    f.seek(base)
    hdr = f.read(8)
    bo = {b"II": "<", b"MM": ">"}.get(hdr[:2])
    if bo is None or len(hdr) < 8 or _u16(bo, hdr[2:4]) != 42:
        raise _Doubt("not a TIFF structure")
    todo = [(_u32(bo, hdr[4:8]), True)]
    seen: set[int] = set()
    slots: list[tuple[int, int, str]] = []
    ifd0_has = False
    while todo and len(seen) < MAX_IFDS:
        off, is_ifd0 = todo.pop(0)
        if off == 0 or off in seen:
            continue
        seen.add(off)
        pos = base + off
        if off < 8 or pos + 2 > limit:
            raise _Doubt("IFD out of bounds")
        f.seek(pos)
        n = _u16(bo, f.read(2))
        if n > MAX_ENTRIES or pos + 2 + 12 * n + 4 > limit:
            raise _Doubt("IFD out of bounds")
        blob = f.read(12 * n + 4)
        if len(blob) != 12 * n + 4:
            raise _Doubt("IFD truncated")
        for i in range(n):
            ent = blob[i * 12:i * 12 + 12]
            tag, typ, cnt = struct.unpack(bo + "HHI", ent[:8])
            if tag == TAG_ORIENTATION:
                if typ != 3 or cnt != 1:
                    raise _Doubt("orientation tag is not one SHORT")
                value = _u16(bo, ent[8:10])
                slots.append((pos + 2 + i * 12 + 8, value, bo))
                if is_ifd0:
                    ifd0_has = True
            elif tag == 330 and typ in (4, 13) and 0 < cnt <= MAX_SUBIFDS:
                if cnt == 1:
                    todo.append((_u32(bo, ent[8:12]), False))
                else:
                    voff = _u32(bo, ent[8:12])
                    if base + voff + 4 * cnt > limit:
                        raise _Doubt("SubIFDs out of bounds")
                    f.seek(base + voff)
                    raw = f.read(4 * cnt)
                    if len(raw) != 4 * cnt:
                        raise _Doubt("SubIFDs truncated")
                    todo.extend((o, False) for o in struct.unpack(bo + "I" * cnt, raw))
        todo.append((_u32(bo, blob[12 * n:12 * n + 4]), False))
    if not ifd0_has:
        raise _Doubt("IFD0 has no orientation tag to change in place")
    return slots


def _jpeg_exif(f, start: int, end: int):
    """(position of the TIFF header, end of the APP1 segment) of the first Exif APP1 in the JPEG that spans
    start..end, or None when it has none. Raises _Doubt on a malformed marker walk."""
    f.seek(start)
    if f.read(2) != b"\xff\xd8":
        raise _Doubt("not a JPEG")
    pos = start + 2
    for _ in range(MAX_SEGMENTS):
        f.seek(pos)
        b = f.read(4)
        if len(b) < 2 or b[0] != 0xFF:
            if len(b) < 2:
                return None
            raise _Doubt("JPEG marker walk lost")
        m = b[1]
        if m == 0xFF:
            pos += 1
            continue
        if m in (0xDA, 0xD9):
            return None
        if m == 0x01 or 0xD0 <= m <= 0xD8:
            pos += 2
            continue
        if len(b) < 4:
            return None
        ln = struct.unpack(">H", b[2:4])[0]
        if ln < 2 or pos + 2 + ln > end:
            raise _Doubt("JPEG segment out of bounds")
        if m == 0xE1 and ln >= 8:
            f.seek(pos + 4)
            if f.read(6) == b"Exif\x00\x00":
                return pos + 10, pos + 2 + ln
        pos += 2 + ln
    return None


def _raf_jpeg(f, size: int):
    f.seek(0)
    head = f.read(0x60)
    if len(head) < 0x5C or head[:15] != b"FUJIFILMCCD-RAW":
        raise _Doubt("not a RAF")
    off, ln = struct.unpack(">II", head[0x54:0x5C])
    if ln < 4 or off < 0x5C or off + ln > size:
        raise _Doubt("RAF embedded JPEG out of bounds")
    return off, off + ln


def _sniff(path: Path, f) -> str:
    ext = path.suffix.lower()
    f.seek(0)
    magic = f.read(16)
    if ext in JPEG_EXTS and magic[:2] == b"\xff\xd8":
        return "jpeg"
    if ext == ".dng" and magic[:4] in (b"II*\x00", b"MM\x00*"):
        return "dng"
    if ext == ".raf" and magic[:15] == b"FUJIFILMCCD-RAW":
        return "raf"
    raise _Doubt(f"{ext or 'this'} files are not written to")


def _slots(path: Path, f, kind: str, size: int):
    """The orientation slots of the file plus the format kind they were found in."""
    if kind == "dng":
        return _walk_tiff(f, 0, size)
    if kind == "raf":
        start, end = _raf_jpeg(f, size)
    else:
        start, end = 0, size
    found = _jpeg_exif(f, start, end)
    if found is None:
        return None
    return _walk_tiff(f, found[0], found[1])


def read_orientation(path) -> int | None:
    """IFD0's orientation (a JPEG's, a DNG's, a RAF's embedded JPEG's), or None when the file has none or is not
    one of the formats written to. Never raises."""
    p = Path(path)
    try:
        with open(p, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            slots = _slots(p, f, _sniff(p, f), size)
    except (OSError, _Doubt, struct.error, ValueError, OverflowError):
        return None
    return slots[0][1] if slots else None


def read_all_orientations(path, kind: str | None = None) -> list[int]:
    """Every orientation value the walker finds (sorted); empty when there is none. ``kind`` ("jpeg", "dng",
    "raf") names the format for a file whose name does not say (the half-written temp). Never raises."""
    p = Path(path)
    try:
        with open(p, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            slots = _slots(p, f, kind or _sniff(p, f), size)
    except (OSError, _Doubt, struct.error, ValueError, OverflowError):
        return []
    return sorted(s[1] for s in slots or ())


# ---- planning a patch ------------------------------------------------------------------------------------

def _minimal_app1(orientation: int) -> bytes:
    tiff = b"MM\x00*" + struct.pack(">I", 8) + struct.pack(">H", 1)
    tiff += struct.pack(">HHI", TAG_ORIENTATION, 3, 1) + struct.pack(">HH", orientation, 0) + struct.pack(">I", 0)
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def plan_patch(path, turns: int) -> Patch | None:
    """The patch that rotates the file at ``path`` by ``turns`` clockwise quarter steps, or None for 0 turns.
    Raises _Doubt (see ``describe``) when the file cannot be patched safely. Reads only the headers."""
    turns = int(turns) % 4
    if turns == 0:
        return None
    p = Path(path)
    with open(p, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        kind = _sniff(p, f)
        slots = _slots(p, f, kind, size)
    if slots is None:  # a JPEG with no Exif at all
        after = compose(1, turns)
        return Patch("jpeg-noexif", 1, after, insert=(2, _minimal_app1(after)), tags=(after,))
    edits, tags = [], []
    for off, value, bo in slots:
        if not 1 <= value <= 8:
            raise _Doubt(f"orientation value {value} is not 1..8")
        new = compose(value, turns)
        edits.append((off, struct.pack(bo + "H", value), struct.pack(bo + "H", new)))
        tags.append(new)
    return Patch(kind, slots[0][1], tags[0], tuple(edits), tags=tuple(sorted(tags)))


def _eq_range(fa, oa: int, fb, ob: int, n: int) -> bool:
    fa.seek(oa)
    fb.seek(ob)
    while n > 0:
        k = min(n, CHUNK)
        a, b = fa.read(k), fb.read(k)
        if len(a) != k or a != b:
            return False
        n -= k
    return True


def matches(src, dst, patch: Patch | None) -> bool:
    """True when ``dst`` is exactly ``src`` with ``patch`` applied (plain equality for None). Never raises."""
    try:
        with open(src, "rb") as fs, open(dst, "rb") as fd:
            size = os.fstat(fs.fileno()).st_size
            segs = patch.segments(size) if patch else [("src", 0, size)]
            total = sum((s[2] - s[1]) if s[0] == "src" else len(s[1]) for s in segs)
            if os.fstat(fd.fileno()).st_size != total:
                return False
            pos = 0
            for s in segs:
                if s[0] == "src":
                    if not _eq_range(fs, s[1], fd, pos, s[2] - s[1]):
                        return False
                    pos += s[2] - s[1]
                else:
                    fd.seek(pos)
                    if fd.read(len(s[1])) != s[1]:
                        return False
                    pos += len(s[1])
        return True
    except OSError:
        return False


def describe(exc: BaseException) -> str:
    return str(exc) if isinstance(exc, _Doubt) else f"{type(exc).__name__}: {exc}"


@functools.lru_cache(maxsize=1)
def _which_exiftool() -> str | None:
    # once per process: on Windows which() is hundreds of os.access calls, and a copy thread making them
    # while the UI thread spins was measured at 7 s (CI windows-latest failed its 10 s wait); alone it is 20 ms
    return shutil.which("exiftool")


def _exiftool_values(exe: str, path: Path) -> list[int]:
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    r = subprocess.run([exe, "-a", "-n", "-s3", "-Orientation", str(path)], capture_output=True,
                       timeout=EXIFTOOL_SECONDS, stdin=subprocess.DEVNULL, **kw)
    if r.returncode != 0:
        raise _Doubt(f"exiftool exited {r.returncode}")
    return sorted(int(x) for x in r.stdout.decode("ascii", "replace").split())


def _pillow_orientation(path: Path, kind: str) -> int | None:
    """IFD0 orientation as Pillow sees it; None when Pillow cannot open the file (not a doubt for raws)."""
    try:
        from PIL import Image

        if kind == "raf":
            with open(path, "rb") as f:
                start, end = _raf_jpeg(f, os.fstat(f.fileno()).st_size)
                f.seek(start)
                src = io.BytesIO(f.read(end - start))
        else:
            src = path
        with Image.open(src) as im:
            return im.getexif().get(TAG_ORIENTATION)
    except Exception:  # noqa: BLE001 - Pillow cannot open every DNG; the other readers still decide
        return None


# ---- applying a patch ------------------------------------------------------------------------------------

def _apply(tmp: Path, patch: Patch) -> None:
    if patch.insert is not None:
        new = tmp.with_name(tmp.name + ".rot")
        try:
            with open(tmp, "rb") as fin, open(new, "wb") as fout:
                off, blob = patch.insert
                fout.write(fin.read(off))
                fout.write(blob)
                shutil.copyfileobj(fin, fout, CHUNK)
                fout.flush()
                os.fsync(fout.fileno())
            os.replace(new, tmp)
        finally:
            try:
                os.unlink(new)
            except OSError:
                pass
        return
    with open(tmp, "r+b") as f:
        for off, old, new in patch.edits:
            f.seek(off)
            if f.read(len(old)) != old:
                raise _Doubt("the copy does not hold the bytes the source did")
            f.seek(off)
            f.write(new)
        f.flush()
        os.fsync(f.fileno())


def _restore(src: Path, tmp: Path) -> None:
    shutil.copyfile(src, tmp)
    if not matches(src, tmp, None):
        raise OSError("could not put the copy back to the original bytes")


def apply_rotation(src, tmp, turns: int, exiftool: Callable[[], str | None] | None = None) -> Outcome:
    """Rotate the copy at ``tmp`` (a byte copy of ``src``, nothing else yet) by ``turns`` clockwise quarter steps.

    Never touches ``src``. On any doubt the copy is rewritten from the source's bytes and the Outcome is "kept"
    with the reason. Only an OSError while putting the copy back is raised: that copy is no good and must fail."""
    src, tmp = Path(src), Path(tmp)
    turns = int(turns) % 4
    if turns == 0:
        return Outcome("none")
    try:
        patch = plan_patch(src, turns)
    except (_Doubt, OSError, struct.error, ValueError, OverflowError) as exc:
        return Outcome("kept", reason=describe(exc))
    assert patch is not None
    out = Outcome("written", patch.before, patch.after)
    try:
        _apply(tmp, patch)
        if not matches(src, tmp, patch):
            raise _Doubt("the copy differs from the source in more than the orientation")
        kind = "jpeg" if patch.kind.startswith("jpeg") else patch.kind
        if read_all_orientations(tmp, kind) != list(patch.tags):
            raise _Doubt("the orientation read back is not the one written")
        out.checked_with.append("spuza")
        pil = _pillow_orientation(tmp, kind)
        if pil is not None:
            if pil != patch.after:
                raise _Doubt(f"Pillow reads orientation {pil}, not {patch.after}")
            out.checked_with.append("pillow")
        exe = (exiftool or _which_exiftool)()
        if exe:
            got = _exiftool_values(exe, tmp)
            if got != list(patch.tags):
                raise _Doubt(f"exiftool reads {got}, not {list(patch.tags)}")
            out.checked_with.append("exiftool")
        return out
    except Exception as exc:  # noqa: BLE001 - subprocess timeouts, odd bytes: all mean "put it back"
        reason = describe(exc)
    _restore(src, tmp)
    return Outcome("kept", reason=reason)
