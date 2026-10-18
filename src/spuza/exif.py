"""Basic EXIF for the inspector: aperture, shutter, ISO, focal length, camera model.

No Qt here. Pillow only. Every reader fails soft: a missing, truncated or hostile EXIF block yields an
empty ``Exif`` (or None), never an exception. Called from ``preview.load`` on the prefetch worker only.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

EXIF_IFD = 0x8769
T_MODEL, T_EXPOSURE, T_FNUMBER, T_ISO, T_FOCAL = 0x0110, 0x829A, 0x829D, 0x8827, 0x920A
T_ORIGINAL, T_DIGITIZED = 0x9003, 0x9004
_DATE = re.compile(r"(\d{4}):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)")


@dataclass(frozen=True)
class Exif:
    """Display-ready strings; None for a value the file does not carry."""

    aperture: str | None = None  # "f/2.8"
    shutter: str | None = None  # "1/250 s"
    iso: str | None = None  # "ISO 200"
    focal: str | None = None  # "18 mm"
    model: str | None = None  # "RICOH GR III"
    taken: str | None = None  # "2026-10-18T21:43:05", the camera's clock as written (original, else digitized)

    def empty(self) -> bool:
        return not any((self.aperture, self.shutter, self.iso, self.focal, self.model, self.taken))


def _num(v) -> float | None:
    if isinstance(v, (tuple, list)):
        v = v[0] if v else None
    if isinstance(v, (bool, bytes, str)) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return f if math.isfinite(f) else None


def _trim(f: float) -> str:
    return f"{f:.1f}".rstrip("0").rstrip(".")


def format_aperture(v) -> str | None:
    f = _num(v)
    return f"f/{_trim(f)}" if f is not None and 0 < f < 1000 else None


def format_shutter(v) -> str | None:
    t = _num(v)
    if t is None or t <= 0:
        return None
    if t < 1:
        return f"1/{max(1, round(1 / t))} s"
    return f"{_trim(t)} s"


def format_iso(v) -> str | None:
    f = _num(v)
    return f"ISO {int(round(f))}" if f is not None and 0 < f < 1e9 else None


def format_focal(v) -> str | None:
    f = _num(v)
    return f"{_trim(f)} mm" if f is not None and 0 < f < 10000 else None


def format_model(v) -> str | None:
    if isinstance(v, bytes):
        v = v.decode("ascii", "ignore")
    if not isinstance(v, str):
        return None
    v = v.replace("\x00", " ").strip()
    return v[:60] or None


def format_taken(v) -> str | None:
    """EXIF "YYYY:MM:DD HH:MM:SS" -> "YYYY-MM-DDTHH:MM:SS", or None when it is not that shape."""
    if isinstance(v, bytes):
        v = v.decode("ascii", "ignore")
    if not isinstance(v, str):
        return None
    m = _DATE.fullmatch(v.replace("\x00", "").strip())
    if not m or m.group(1) == "0000":
        return None
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}"


def _from_exif(ex) -> Exif:
    try:
        sub = ex.get_ifd(EXIF_IFD)
    except Exception:
        sub = {}
    if not hasattr(sub, "get"):
        sub = {}

    def pick(tag):
        v = sub.get(tag)
        return ex.get(tag) if v is None else v

    iso = pick(T_ISO)
    return Exif(
        aperture=format_aperture(pick(T_FNUMBER)),
        shutter=format_shutter(pick(T_EXPOSURE)),
        iso=format_iso(iso),
        focal=format_focal(pick(T_FOCAL)),
        model=format_model(ex.get(T_MODEL)),
        taken=format_taken(pick(T_ORIGINAL)) or format_taken(pick(T_DIGITIZED)),
    )


UNREAD = object()  # sentinel: "EXIF not read yet" (None already means "read, none found")


def from_image(im) -> Exif:
    """EXIF of an already-open (and, for PNG, already-loaded) image. Never raises."""
    try:
        return _from_exif(im.getexif())
    except Exception:
        return Exif()


def _open_read(src) -> Exif:
    with Image.open(src) as im:
        return _from_exif(im.getexif())


def read(data: bytes | None = None, path=None) -> Exif | None:
    """EXIF from in-memory image bytes, falling back to the file itself (DNG header, JPEG, HEIC).

    Returns None when neither carries any of the basic fields. Never raises.
    """
    if data:
        try:
            ex = _open_read(io.BytesIO(data))
            if not ex.empty():
                return ex
        except Exception:
            pass
    if path is not None:
        try:
            ex = _open_read(Path(path))
            if not ex.empty():
                return ex
        except Exception:
            pass
    return None
