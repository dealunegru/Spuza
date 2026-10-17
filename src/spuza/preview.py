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
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image

from spuza import exif as _exif

# .arw .nef .nrw .pef .3fr: the TIFF walk finds the full JPEG. .cr2 needs the SOF check (_tiff_slice sof_check).
# .orf .rw2 .cr3 are not plain TIFF: they reach rawpy's extract_thumb. Trap (not fixed): on the Panasonic S5 II
# rawpy returns the 1920 px JpgFromRaw while a 6000 px JpgFromRaw2 sits in IFD0 (raws.md finding 4).
RAW_EXTS = {".raf", ".dng", ".arw", ".nef", ".nrw", ".pef", ".3fr", ".cr2", ".orf", ".rw2", ".cr3"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".hif"}
HEIF_EXTS = {".heic", ".heif", ".hif"}

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
    source: str  # raf-embedded | dng-embedded | rawpy-thumb | rawpy-full | pillow | heif | hif
    width: int  # native pixel size of the decoded preview (before draft scaling)
    height: int
    nbytes: int  # preview payload bytes read (file size for open formats)
    ms: float  # wall time of load()
    exif: _exif.Exif | None = None  # basic EXIF read on the worker; None when the file carries none
    focus: object | None = None  # spuza.focus.FocusInfo, read on the worker only while focus_point is on
    native: bool | None = None  # set by load_native(): True = the pixels are the picture's own, False = best effort


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


_DCT_SOF = {0xC0, 0xC1, 0xC2}  # baseline, extended, progressive; SOF3 is a lossless raw strip


def _is_dct_jpeg(f, off: int, ln: int, size: int) -> bool:
    """True when the JPEG at off starts a lossy (DCT) frame. Walks marker segments only, capped."""
    if ln < 4 or off + ln > size:
        return False
    f.seek(off)
    if f.read(2) != b"\xff\xd8":
        return False
    pos, end = off + 2, off + ln
    for _ in range(256):
        if pos + 4 > end:
            return False
        f.seek(pos)
        b = f.read(4)
        if len(b) < 4 or b[0] != 0xFF:
            return False
        if b[1] == 0xFF:  # fill byte
            pos += 1
            continue
        if b[1] in (0xDA, 0xD9):
            return False
        if 0xC0 <= b[1] <= 0xCF and b[1] not in (0xC4, 0xC8, 0xCC):
            return b[1] in _DCT_SOF
        pos += 2 + struct.unpack(">H", b[2:4])[0]
    return False


def _tiff_slice(f, size: int, sof_check: bool = False) -> tuple[bytes, int] | None:
    """Widest embedded JPEG in a TIFF/DNG plus the IFD0 orientation (0 when absent).

    sof_check (CR2): a single JPEG strip without NewSubfileType also counts when its SOF is a DCT frame; the
    lossless raw strip (SOF3) is refused by marker, so the 160x120 IFD1 thumbnail is never the widest."""
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
        elif (sof_check and comp in (6, 7) and len(ents.get(273, ())) == 1 and ents.get(279)
              and _is_dct_jpeg(f, ents[273][0], ents[279][0], size)):
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
            res = _tiff_slice(f, size, sof_check=ext == ".cr2")
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


def _decode(src, max_side: int | None, fallback_orient: int = 0, exif_out: list | None = None):
    """Open, draft-scale, load, orient and convert; return (RGB image, native (w, h)).

    exif_out: when given, the loaded image's EXIF is appended so the caller need not reopen (and, for PNG,
    decode a second time just to read it)."""
    if isinstance(src, bytes):
        src = io.BytesIO(src)
    try:
        with Image.open(src) as im:
            native = im.size
            if max_side and max(native) > max_side and im.format in ("JPEG", "MPO"):  # Pillow reports gain-map / Ultra HDR JPEGs as MPO
                long_ = max(native)
                im.draft("RGB", (-(-native[0] * max_side // long_), -(-native[1] * max_side // long_)))
            im.load()
            if exif_out is not None:
                exif_out.append(_exif.from_image(im))
            out = _to_8bit(_orient(im, fallback_orient)).convert("RGB")
            return out, native
    except Exception as e:
        raise PreviewError(f"cannot decode image: {e}") from e


def _frame_box(s) -> tuple[int, int, int, int] | None:
    """The camera's crop (DefaultCropOrigin / DefaultCropSize) as (left, top, width, height) in the frame
    rawpy's postprocess returns (visible area, after the flip); None when it is absent, empty or outside it."""
    try:
        w, h, flip = int(s.width), int(s.height), int(s.flip)
        left, top = int(s.crop_left_margin) - int(s.left_margin), int(s.crop_top_margin) - int(s.top_margin)
        cw, ch = int(s.crop_width), int(s.crop_height)
    except (AttributeError, TypeError, ValueError):
        return None
    if cw <= 0 or ch <= 0 or left < 0 or top < 0 or left + cw > w or top + ch > h:
        return None
    if flip == 3:
        return w - (left + cw), h - (top + ch), cw, ch
    if flip == 5:  # 90 counter-clockwise
        return top, w - (left + cw), ch, cw
    if flip == 6:  # 90 clockwise
        return h - (top + ch), left, ch, cw
    return left, top, cw, ch


def _rawpy_full(path: Path, half_size: bool, crop: bool = True) -> Image.Image:
    """Demosaic with the camera white balance, cropped to the camera's frame (an in-camera crop mode keeps
    the sensor's margins in the raw; they are never part of the picture). crop=False keeps the whole visible
    sensor (what the crop frame was cut from)."""
    try:
        import rawpy

        with rawpy.imread(str(path)) as r:
            arr = r.postprocess(use_camera_wb=True, half_size=half_size)
            box = _frame_box(r.sizes) if crop else None
            flip = int(r.sizes.flip)
            vis = (int(r.sizes.height), int(r.sizes.width))
        if box is not None:
            oh, ow = (vis[1], vis[0]) if flip in (5, 6) else vis  # the visible area as returned, after the flip
            fy, fx = arr.shape[0] / max(1, oh), arr.shape[1] / max(1, ow)  # half_size halves it
            x0, y0 = round(box[0] * fx), round(box[1] * fy)
            x1, y1 = min(arr.shape[1], round((box[0] + box[2]) * fx)), min(arr.shape[0], round((box[1] + box[3]) * fy))
            if x1 > x0 and y1 > y0:
                arr = arr[y0:y1, x0:x1]
        return Image.fromarray(arr).convert("RGB")
    except Exception as e:
        raise PreviewError(f"cannot demosaic {path.name}: {e}") from e


def _focus(path: Path, source: str, data):
    """Focus data for a RAF while focus_point is on, else None; reuses the embedded JPEG already in memory.

    Only the camera's own embedded JPEG is in the frame the AF point was probed in (the lead's focus probe):
    the rawpy-thumb / rawpy-full fallbacks are not, so they carry no focus."""
    from spuza import features

    if not features.enabled("focus_point") or source != "raf-embedded" or not data:
        return None
    from spuza import focus

    return focus.from_jpeg(data)


def load(path, max_side: int | None = 2560):
    """Return (RGB image, PreviewInfo). Raises PreviewError for unknown or corrupt files."""
    path = Path(path)
    t0 = time.perf_counter()
    ext = path.suffix.lower()
    try:
        fsize = path.stat().st_size
    except OSError as e:
        raise PreviewError(f"cannot stat {path}: {e}") from e

    def done(im, source, native, nbytes, data=None, ex=_exif.UNREAD):
        if ex is _exif.UNREAD:
            ex = _exif.read(data, path)  # fails soft; bytes already in memory first, file header second
        fi = _focus(path, source, data) if ext == ".raf" else None
        ms = (time.perf_counter() - t0) * 1000
        return im, PreviewInfo(path, source, native[0], native[1], nbytes, ms, ex, fi)

    if ext in RAW_EXTS:
        got = _slice(path)
        if got:
            try:
                im, native = _decode(got[0], max_side, got[2])
                return done(im, got[1], native, len(got[0]), got[0])
            except PreviewError:
                pass
        data = _rawpy_thumb(path)
        if data:
            try:
                im, native = _decode(data, max_side)
                return done(im, "rawpy-thumb", native, len(data), data)
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
        return done(im, "hif" if ext == ".hif" else "heif", native, fsize)
    if ext == ".png":
        got: list = []  # PngImageFile.getexif() decodes the whole image; take it from the one decode we did
        im, native = _decode(path, max_side, exif_out=got)
        ex = got[0] if got else None
        return done(im, "pillow", native, fsize, ex=None if ex is None or ex.empty() else ex)
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


def frame_size(path) -> tuple[int, int] | None:
    """Width and height of the camera's frame (its crop; the whole visible sensor when none), else None."""
    path = Path(path)
    if path.suffix.lower() not in RAW_EXTS:
        return None
    try:
        import rawpy

        with rawpy.imread(str(path)) as r:
            s = r.sizes
            box = _frame_box(s)
            if box is not None:
                return box[2], box[3]
            w, h, flip = int(s.width), int(s.height), int(s.flip)
        return (h, w) if flip in (5, 6) else (w, h)
    except Exception:
        return None


def needs_full_decode(path, info: PreviewInfo) -> bool:
    """True when a raw's embedded preview is much smaller than the camera's FRAME (dec-044).

    The frame is the crop the camera shot (a crop mode makes the preview small against the sensor but not
    against the frame, and the embedded preview already is that frame)."""
    frame = frame_size(path)
    if frame is None:
        return False
    return max(info.width, info.height) < 0.6 * max(frame)


def full_decode(path, half_size: bool = False, crop: bool = True) -> Image.Image:
    """Demosaic a raw with the camera white balance; oriented RGB. crop=False keeps the whole visible sensor."""
    return _rawpy_full(Path(path), half_size, crop)


SENSOR_CROP_FRACTION = 0.97  # a frame at least this much of the sensor in both directions is not "cropped in camera"


def sensor_geometry(path) -> tuple[tuple[int, int, int, int], tuple[int, int]] | None:
    """For a raw the camera cropped in-camera: ((left, top, width, height) of the crop frame, (width, height) of
    the whole visible sensor), both in the frame rawpy returns. None for a non-raw, an uncropped raw, or when
    rawpy cannot say (the caller then treats the picture as uncropped)."""
    path = Path(path)
    if path.suffix.lower() not in RAW_EXTS:
        return None
    try:
        import rawpy

        with rawpy.imread(str(path)) as r:
            s = r.sizes
            box = _frame_box(s)
            w, h, flip = int(s.width), int(s.height), int(s.flip)
    except Exception:
        return None
    if flip in (5, 6):
        w, h = h, w
    if box is None or (box[2] >= SENSOR_CROP_FRACTION * w and box[3] >= SENSOR_CROP_FRACTION * h):
        return None  # no crop, or only the margin of the sensor that a plain DNG always leaves out
    return box, (w, h)


def is_capped(shown: tuple[int, int], info) -> bool:
    """True when a decode of `shown` (w, h) was scaled down from the preview's NATIVE size in `info`.

    The capped fast decode (load's max_side) and the native size are different pictures at 1:1: Z must compare
    the native size, never the decode on screen. Unknown or malformed info is "not capped" (nothing to fetch)."""
    try:
        return max(int(shown[0]), int(shown[1])) < max(int(info.width), int(info.height))
    except (AttributeError, TypeError, ValueError, IndexError):
        return False


def native_preview(path) -> Image.Image:
    """The file's own preview or image at its native size (no max_side cap), oriented RGB."""
    return load(path, None)[0]


def load_native(path):
    """(RGB image, PreviewInfo) at the picture's native pixels, for Z.

    A plain image, or a raw whose embedded preview is at least 0.6 of the sensor, is decoded at the preview's
    native size; a raw with a smaller preview is demosaiced. If the demosaic fails the preview comes back with
    info.native False, so the caller can say it is not the real pixels."""
    path = Path(path)
    im, info = load(path, None)
    if path.suffix.lower() in RAW_EXTS and needs_full_decode(path, info):
        try:
            full = _rawpy_full(path, False)
        except PreviewError:
            return im, replace(info, native=False)
        return full, replace(info, source="rawpy-full", width=full.size[0], height=full.size[1], native=True)
    return im, replace(info, native=True)


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
