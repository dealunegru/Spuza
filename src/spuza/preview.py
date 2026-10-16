"""Fast previews for the culling view: embedded JPEG slicing for raws, Pillow for open formats.

No Qt here. Raws are previewed from the JPEG the camera already embedded (RAF header offsets, TIFF/DNG
IFD walk), falling back to rawpy's thumbnail and finally to a half-size demosaic. Every read of a
container is bounds-checked against the file size and every IFD walk is capped, so a truncated or
hostile file yields None / PreviewError, never a hang or an unbounded read.
"""

from __future__ import annotations

import io
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

RAW_EXTS = {".raf", ".dng"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
HEIF_EXTS = {".heic", ".heif"}

MAX_IFDS = 64  # cap against cyclic / hostile IFD chains
MAX_ENTRIES = 4096  # per IFD
MAX_VALUES = 64  # array values read per tag
MAX_PREVIEW_BYTES = 256 * 1024 * 1024  # never slice more than this
_FMT = {1: "B", 3: "H", 4: "I"}
_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


class PreviewError(Exception):
    """The file could not be turned into a preview."""


@dataclass(frozen=True)
class PreviewInfo:
    path: Path
    source: str  # raf-embedded | dng-embedded | rawpy-thumb | rawpy-full | pillow | heif
    width: int  # native pixel size of the decoded preview (before draft scaling)
    height: int
    nbytes: int  # preview payload bytes read (file size for open formats)
    ms: float  # wall time of load()


# ---- container slicing ------------------------------------------------------------------------

def _raf_slice(f, size: int) -> bytes | None:
    head = f.read(0x60)
    if len(head) < 0x5C or head[:15] != b"FUJIFILMCCD-RAW":
        return None
    off, ln = struct.unpack(">II", head[0x54:0x5C])
    if ln < 4 or ln > MAX_PREVIEW_BYTES or off + ln > size:
        return None
    f.seek(off)
    data = f.read(ln)
    return data if len(data) == ln and data[:2] == b"\xff\xd8" else None


def _read_ifd(f, bo: str, off: int, size: int):
    """Return (entries dict tag -> tuple of ints, next_ifd) or None when out of bounds."""
    if off < 8 or off + 2 > size:
        return None
    f.seek(off)
    n = struct.unpack(bo + "H", f.read(2))[0]
    if n > MAX_ENTRIES or off + 2 + 12 * n + 4 > size:
        return None
    blob = f.read(12 * n + 4)
    if len(blob) != 12 * n + 4:
        return None
    ents: dict[int, tuple] = {}
    for i in range(n):
        tag, typ, cnt = struct.unpack(bo + "HHI", blob[i * 12:i * 12 + 8])
        slot = blob[i * 12 + 8:i * 12 + 12]
        fmt = _FMT.get(typ)
        if fmt is None or cnt == 0:
            ents[tag] = ()
            continue
        sz = struct.calcsize(fmt)
        k = min(cnt, MAX_VALUES)
        if sz * cnt <= 4:
            ents[tag] = struct.unpack(bo + fmt * k, slot[:sz * k])
            continue
        voff = struct.unpack(bo + "I", slot)[0]
        if voff + sz * k > size:
            ents[tag] = ()
            continue
        pos = f.tell()
        f.seek(voff)
        raw = f.read(sz * k)
        f.seek(pos)
        ents[tag] = struct.unpack(bo + fmt * k, raw) if len(raw) == sz * k else ()
    return ents, struct.unpack(bo + "I", blob[12 * n:12 * n + 4])[0]


def _tiff_slice(f, size: int) -> tuple[bytes, int] | None:
    """Widest embedded JPEG in a TIFF/DNG plus the IFD0 orientation (0 when absent)."""
    hdr = f.read(8)
    bo = {b"II": "<", b"MM": ">"}.get(hdr[:2])
    if bo is None or len(hdr) < 8 or struct.unpack(bo + "H", hdr[2:4])[0] != 42:
        return None
    todo = [struct.unpack(bo + "I", hdr[4:8])[0]]
    seen: set[int] = set()
    found: list[tuple[int, int, int]] = []  # (width, offset, length)
    orient = 0
    first = True
    while todo and len(seen) < MAX_IFDS:
        off = todo.pop()
        if off == 0 or off in seen:
            continue
        seen.add(off)
        res = _read_ifd(f, bo, off, size)
        if res is None:
            first = False
            continue
        ents, nxt = res
        if first:
            orient = (ents.get(274) or (0,))[0]
            first = False
        todo.append(nxt)
        todo.extend(ents.get(330, ()))
        comp = (ents.get(259) or (0,))[0]
        w = (ents.get(256) or (0,))[0]
        subtype = (ents.get(254) or (0,))[0]
        if ents.get(513) and ents.get(514):
            found.append((w, ents[513][0], ents[514][0]))
        elif comp in (6, 7) and len(ents.get(273, ())) == 1 and ents.get(279) and subtype & 1:
            found.append((w, ents[273][0], ents[279][0]))
    for _w, off, ln in sorted(found, key=lambda t: (t[0], t[2]), reverse=True):
        if ln < 4 or ln > MAX_PREVIEW_BYTES or off + ln > size:
            continue
        f.seek(off)
        data = f.read(ln)
        if len(data) == ln and data[:2] == b"\xff\xd8":
            return data, orient
    return None


def _slice(path: Path) -> tuple[bytes, str, int] | None:
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if ext == ".raf":
                data = _raf_slice(f, size)
                return (data, "raf-embedded", 0) if data else None
            res = _tiff_slice(f, size)
            return (res[0], "dng-embedded", res[1]) if res else None
    except (OSError, struct.error, ValueError, OverflowError):
        return None


def _rawpy_thumb(path: Path) -> bytes | None:
    try:
        import rawpy

        with rawpy.imread(str(path)) as r:
            t = r.extract_thumb()
        if t.format == rawpy.ThumbFormat.JPEG:
            return bytes(t.data)
    except Exception:
        return None
    return None


def extract_embedded(path) -> tuple[bytes, str] | None:
    """Embedded JPEG of a raw file as (bytes, kind), or None. Raws only."""
    path = Path(path)
    if path.suffix.lower() not in RAW_EXTS:
        return None
    got = _slice(path)
    if got:
        return got[0], got[1]
    data = _rawpy_thumb(path)
    return (data, "rawpy-thumb") if data else None


# ---- decoding ---------------------------------------------------------------------------------

def _orient(im: Image.Image, fallback: int = 0) -> Image.Image:
    try:
        o = im.getexif().get(274) or fallback
    except Exception:
        o = fallback
    op = _TRANSPOSE.get(o)
    return im.transpose(op) if op is not None else im


def _to_8bit(im: Image.Image) -> Image.Image:
    """Scale high-bit-depth grayscale (I;16, I;16B, I, F) to 8-bit; other modes pass through."""
    if im.mode not in ("I;16", "I;16L", "I;16B", "I", "F"):
        return im
    import numpy as np

    a = np.asarray(im).astype("float64")
    lo, hi = float(a.min()), float(a.max())
    if im.mode.startswith("I;16") or (im.mode == "I" and lo >= 0 and hi <= 65535):
        lo, hi = 0.0, 65535.0  # true 16-bit range
    if hi <= lo:
        return Image.new("L", im.size, 0)
    return Image.fromarray(np.clip((a - lo) * (255.0 / (hi - lo)) + 0.5, 0, 255).astype("uint8"), "L")


def _decode(src, max_side: int | None, fallback_orient: int = 0):
    """Open, draft-scale, load, orient and convert; return (RGB image, native (w, h))."""
    if isinstance(src, bytes):
        src = io.BytesIO(src)
    try:
        with Image.open(src) as im:
            native = im.size
            if max_side and max(native) > max_side and im.format == "JPEG":
                long_ = max(native)
                im.draft("RGB", (-(-native[0] * max_side // long_), -(-native[1] * max_side // long_)))
            im.load()
            out = _to_8bit(_orient(im, fallback_orient)).convert("RGB")
            return out, native
    except Exception as e:
        raise PreviewError(f"cannot decode image: {e}") from e


def _rawpy_full(path: Path, half_size: bool) -> Image.Image:
    try:
        import rawpy

        with rawpy.imread(str(path)) as r:
            arr = r.postprocess(use_camera_wb=True, half_size=half_size)
        return Image.fromarray(arr).convert("RGB")
    except Exception as e:
        raise PreviewError(f"cannot demosaic {path.name}: {e}") from e


def load(path, max_side: int | None = 2560):
    """Return (RGB image, PreviewInfo). Raises PreviewError for unknown or corrupt files."""
    path = Path(path)
    t0 = time.perf_counter()
    ext = path.suffix.lower()
    try:
        fsize = path.stat().st_size
    except OSError as e:
        raise PreviewError(f"cannot stat {path}: {e}") from e

    def done(im, source, native, nbytes):
        ms = (time.perf_counter() - t0) * 1000
        return im, PreviewInfo(path, source, native[0], native[1], nbytes, ms)

    if ext in RAW_EXTS:
        got = _slice(path)
        if got:
            try:
                im, native = _decode(got[0], max_side, got[2])
                return done(im, got[1], native, len(got[0]))
            except PreviewError:
                pass
        data = _rawpy_thumb(path)
        if data:
            try:
                im, native = _decode(data, max_side)
                return done(im, "rawpy-thumb", native, len(data))
            except PreviewError:
                pass
        try:
            im = _rawpy_full(path, True)
            return done(im, "rawpy-full", im.size, fsize)
        except PreviewError:
            # Not a real raw (e.g. a JPEG named .DNG): let Pillow try before giving up.
            try:
                im, native = _decode(path, max_side)
            except PreviewError:
                pass
            else:
                return done(im, "pillow", native, fsize)
            raise
    if ext in HEIF_EXTS:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except Exception as e:
            raise PreviewError(f"HEIF support unavailable: {e}") from e
        im, native = _decode(path, max_side)
        return done(im, "heif", native, fsize)
    if ext in IMAGE_EXTS:
        im, native = _decode(path, max_side)
        return done(im, "pillow", native, fsize)
    raise PreviewError(f"unsupported file type: {path.suffix or path.name}")


# ---- raw helpers ------------------------------------------------------------------------------

def sensor_size(path) -> tuple[int, int] | None:
    """Visible sensor (width, height) after rawpy's flip, for raw files; else None."""
    path = Path(path)
    if path.suffix.lower() not in RAW_EXTS:
        return None
    try:
        import rawpy

        with rawpy.imread(str(path)) as r:
            s = r.sizes
            w, h, flip = int(s.width), int(s.height), int(s.flip)
        return (h, w) if flip in (5, 6) else (w, h)
    except Exception:
        return None


def needs_full_decode(path, info: PreviewInfo) -> bool:
    """True when a raw's embedded preview is much smaller than the sensor (dec-044)."""
    sensor = sensor_size(path)
    if sensor is None:
        return False
    return max(info.width, info.height) < 0.6 * max(sensor)


def full_decode(path, half_size: bool = False) -> Image.Image:
    """Demosaic a raw with the camera white balance; oriented RGB."""
    return _rawpy_full(Path(path), half_size)


def report(path) -> dict:
    path = Path(path)
    out = {"file": path.name, "size_bytes": None, "source": None, "width": None,
           "height": None, "ms": None, "error": None}
    try:
        out["size_bytes"] = path.stat().st_size
        _im, info = load(path)
        out.update(source=info.source, width=info.width, height=info.height, ms=round(info.ms, 2))
    except (PreviewError, OSError) as e:
        out["error"] = str(e)
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    rc = 0
    for a in args:
        r = report(a)
        if r["error"]:
            rc = 1
            print(f"{r['file']}: ERROR {r['error']}")
        else:
            print(f"{r['file']}: {r['source']} {r['width']}x{r['height']} "
                  f"{r['size_bytes']} bytes {r['ms']} ms")
    return rc


if __name__ == "__main__":
    sys.exit(main())
