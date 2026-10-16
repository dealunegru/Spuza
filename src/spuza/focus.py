"""The camera's AF point from the Fuji makernote (flag focus_point, advanced-only).

No Qt here. Fails soft: a missing, truncated or hostile makernote yields None, never an exception. Called from
`preview.load` on the prefetch worker only, and only while `features.enabled("focus_point")`.
FocusPixel (0x1023) is in the pixel frame of the RAF's embedded JPEG: confirmed by the lead's probe
(state/plans/spuza-features-r1/focus-probe.md in MrAgent) on the models in ALLOWLIST. Owned by task spz-nx-focus.

Code tables, from exiftool's FujiFilm tag table (13.58), checked against the sample files:
FocusMode 0x1021: 0 Auto, 1 Manual, 65535 Movie. Area: FocusSettings 0x102D (int32) AFAreaMode = bits 8-11:
0 Single Point, 1 Zone, 2 Wide/Tracking; when 0x102D is absent, AFMode 0x1022: 1 Single Point, 256 Zone,
512 Wide/Tracking. Every read is bounds-checked and every walk capped, like preview.py.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

# Bodies whose frame the probe confirmed in AF (X-T4 and X-T5 Single Point; X100V tracking, X-T50 zone).
ALLOWLIST = frozenset({"X-T4", "X-T5", "X100V", "X-T50"})

MAX_HEAD = 256 * 1024  # bytes of the embedded JPEG read: its EXIF APP1 (<= 64 KB) and the SOF marker
MAX_ENTRIES = 4096
MAX_SEGMENTS = 64
_FOCUS_MODES = {0: "Auto", 1: "Manual", 65535: "Movie"}
_AREA_BITS = {0: "Single Point", 1: "Zone", 2: "Wide/Tracking"}
_AF_MODES = {1: "Single Point", 256: "Zone", 512: "Wide/Tracking"}
_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 7: 1, 8: 2, 9: 4}  # TIFF type -> bytes per value
_FMT = {1: "B", 3: "H", 4: "I", 7: "B", 8: "h", 9: "i"}


@dataclass(frozen=True)
class FocusInfo:
    model: str | None  # "X-T5"
    mode: str | None  # FocusMode, e.g. "Auto", "Manual"
    area: str | None  # AF area, e.g. "Single Point", "Zone", "Wide/Tracking"
    point: tuple[int, int] | None  # FocusPixel (x, y) in the embedded JPEG's stored frame; None without one
    frame: tuple[int, int] | None  # that JPEG's stored (width, height)
    orientation: int = 1  # EXIF orientation of that JPEG; frac() maps the point through it

    def usable(self) -> bool:
        """A point we may show: present, inside its frame, an allowlisted model, and not manual focus."""
        if self.point is None or self.frame is None or self.model not in ALLOWLIST:
            return False
        if self.mode == "Manual":
            return False
        x, y = self.point
        w, h = self.frame
        return w > 0 and h > 0 and 0 <= x < w and 0 <= y < h

    def frac(self) -> tuple[float, float] | None:
        """(fx, fy) of the point in the DISPLAYED (oriented) picture, each 0-1; None unless usable()."""
        if not self.usable():
            return None
        fx, fy = self.point[0] / self.frame[0], self.point[1] / self.frame[1]
        return {
            2: (1 - fx, fy), 3: (1 - fx, 1 - fy), 4: (fx, 1 - fy),
            5: (fy, fx), 6: (1 - fy, fx), 7: (1 - fy, 1 - fx), 8: (fy, 1 - fx),
        }.get(self.orientation, (fx, fy))

    def describe(self) -> str:
        """The inspector line: "Focus: point (2207, 1472) · Auto Single Point", "Focus: manual", or
        "Focus: no point"; a model off the allowlist says its point is not verified."""
        if self.mode == "Manual":
            return "Focus: manual"
        if self.point is None:
            return "Focus: no point"
        if self.model not in ALLOWLIST:
            return f"Focus: point not verified for {self.model or 'this camera'}"
        if not self.usable():
            return "Focus: no point"
        tail = " ".join(s for s in (self.mode, self.area) if s)
        return f"Focus: point ({self.point[0]}, {self.point[1]})" + (f" · {tail}" if tail else "")


def _ifd(buf: bytes, bo: str, off: int) -> dict[int, tuple] | None:
    """Entries of the IFD at buf[off:] as tag -> tuple of ints (a 1-tuple of bytes for ASCII); value
    offsets are relative to buf. None when out of bounds. Entries pointing out of bounds are skipped."""
    if off < 0 or off + 2 > len(buf):
        return None
    n = struct.unpack_from(bo + "H", buf, off)[0]
    if n > MAX_ENTRIES or off + 2 + 12 * n > len(buf):
        return None
    out: dict[int, tuple] = {}
    for i in range(n):
        e = off + 2 + 12 * i
        tag, typ, cnt = struct.unpack_from(bo + "HHI", buf, e)
        sz = _SIZES.get(typ)
        if sz is None or cnt == 0:
            continue
        k = min(cnt, 256 if typ == 2 else 64)
        pos = e + 8 if sz * cnt <= 4 else struct.unpack_from(bo + "I", buf, e + 8)[0]
        if pos + sz * k > len(buf):
            continue
        if typ == 2:
            out[tag] = (bytes(buf[pos:pos + k]),)
        else:
            out[tag] = struct.unpack_from(bo + _FMT[typ] * k, buf, pos)
    return out


def _segments(buf: bytes):
    """Yield (marker, start-of-payload, payload length) of JPEG segments up to the scan, capped."""
    pos = 2
    for _ in range(MAX_SEGMENTS):
        if pos + 4 > len(buf) or buf[pos] != 0xFF:
            return
        m = buf[pos + 1]
        if m == 0xFF:
            pos += 1
            continue
        ln = struct.unpack_from(">H", buf, pos + 2)[0]
        if ln < 2:
            return
        yield m, pos + 4, ln - 2
        if m == 0xDA:
            return
        pos += 2 + ln


def _sof_size(buf: bytes) -> tuple[int, int] | None:
    for m, p, ln in _segments(buf):
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC) and ln >= 5 and p + 5 <= len(buf):
            h, w = struct.unpack_from(">HH", buf, p + 1)
            return (w, h) if w and h else None
    return None


def _exif_block(buf: bytes) -> bytes | None:
    for m, p, ln in _segments(buf):
        if m == 0xE1 and buf[p:p + 6] == b"Exif\0\0":
            return bytes(buf[p + 6:p + ln])
    return None


def _note(tiff: bytes, bo: str, exif_off: int) -> bytes | None:
    """The MakerNote value (0x927C) of the Exif IFD at `exif_off`."""
    if exif_off < 0 or exif_off + 2 > len(tiff):
        return None
    n = struct.unpack_from(bo + "H", tiff, exif_off)[0]
    for i in range(min(n, MAX_ENTRIES)):
        e = exif_off + 2 + 12 * i
        if e + 12 > len(tiff):
            return None
        tag, _typ, cnt = struct.unpack_from(bo + "HHI", tiff, e)
        if tag == 0x927C:
            start = e + 8 if cnt <= 4 else struct.unpack_from(bo + "I", tiff, e + 8)[0]
            return tiff[start:start + cnt] if start + cnt <= len(tiff) else None
    return None


def _from_jpeg(buf: bytes) -> FocusInfo | None:
    if buf[:2] != b"\xff\xd8":
        return None
    tiff = _exif_block(buf)
    if tiff is None or len(tiff) < 8:
        return None
    bo = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if bo is None or struct.unpack_from(bo + "H", tiff, 2)[0] != 42:
        return None
    ifd0 = _ifd(tiff, bo, struct.unpack_from(bo + "I", tiff, 4)[0])
    sub = (ifd0 or {}).get(0x8769)
    if not sub:
        return None
    mn = _note(tiff, bo, sub[0])
    # Fuji: "FUJIFILM" + LE offset of the IFD; value offsets inside are relative to the note's start.
    if mn is None or mn[:8] != b"FUJIFILM" or len(mn) < 12:
        return None
    entries = _ifd(mn, "<", struct.unpack_from("<I", mn, 8)[0])
    if not entries:
        return None
    model = (ifd0.get(0x0110) or (b"",))[0].split(b"\0")[0].decode("ascii", "replace").strip()
    if model.upper().startswith("FUJIFILM "):
        model = model[9:].strip()
    fm = entries.get(0x1021)
    mode = _FOCUS_MODES.get(fm[0]) if fm else None
    fs, af = entries.get(0x102D), entries.get(0x1022)
    area = None
    if mode != "Manual":
        area = _AREA_BITS.get((fs[0] >> 8) & 0xF) if fs else None
        if area is None and af:
            area = _AF_MODES.get(af[0])
    fp = entries.get(0x1023)
    point = (int(fp[0]), int(fp[1])) if fp and len(fp) >= 2 else None
    ori = (ifd0.get(0x0112) or (1,))[0]
    return FocusInfo(model or None, mode, area, point, _sof_size(buf), ori if 1 <= ori <= 8 else 1)


def from_jpeg(data) -> FocusInfo | None:
    """Focus data from the bytes (only the first 256 KB are used) of a Fuji RAF's embedded JPEG. Never raises."""
    try:
        return _from_jpeg(bytes(data[:MAX_HEAD]))
    except Exception:  # noqa: BLE001 - hostile bytes must never escape (struct/Index/Type/Memory...)
        return None


def read(path) -> FocusInfo | None:
    """The focus data of a RAF at `path`, or None for any other file or when it carries none. Never raises."""
    try:
        path = Path(path)
        if path.suffix.lower() != ".raf":
            return None
        size = path.stat().st_size
        with open(path, "rb") as f:
            head = f.read(0x60)
            if len(head) < 0x5C or head[:15] != b"FUJIFILMCCD-RAW":
                return None
            off, ln = struct.unpack(">II", head[0x54:0x5C])
            if ln < 4 or off + ln > size:
                return None
            f.seek(off)
            data = f.read(min(ln, MAX_HEAD))
        return from_jpeg(data)
    except Exception:  # noqa: BLE001 - never raises
        return None
