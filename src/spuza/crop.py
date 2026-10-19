"""Crop model and pixel pipeline: a cropped copy is a new JPEG, written beside the untouched original.

Plain Python plus Pillow, no widgets. A crop is ``Crop = (left, top, right, bottom)``, fractions 0..1 of the
picture in its own decoded, EXIF-oriented frame, BEFORE the user's quarter turns (the frame the viewer's
unrotated pixmap uses).

API
    normalise(rect) -> Crop | None       clamp to [0, 1], order the corners; None for garbage (not a 4-sequence of
                                         finite real numbers, bools included) or when a side is under MIN_SIDE (1 %)
    crop_box(size, crop) -> (x0, y0, x1, y1)   the crop in pixels of a picture of ``size``; never empty
    CropJob(primary, sidecar, crop, turns, ratio=None)   one picture to crop (frozen); ``sidecar`` may be None;
                                         ``ratio`` (a spuza.ratio.Ratio, None = Free) only names the export file
    render(primary, sidecar, crop, turns) -> PIL.Image   best pixels, cropped, then ``turns`` clockwise quarter turns
                                         baked in; ``im.info`` carries the "exif" bytes (Orientation 1) and the
                                         "icc_profile" when the source had them. Raises CropError, nothing else.
    best_pixels(primary, sidecar) -> PIL.Image   the best-pixels step alone (RGB, EXIF-oriented, not turned, same
                                         ``info``); shared with the story export. Raises CropError, nothing else.
    write_jpeg(im, dst)                  save at JPEG quality 95 with that EXIF and profile; Orientation is 1
    CropError                            the one error render/write_jpeg raise

Best pixels (the rule Z uses): a .jpg/.jpeg sidecar is the picture's own pixels; a non-raw primary is decoded
whole, EXIF-transposed; a raw is demosaiced (preview.full_decode, cropped to the camera frame) when
preview.needs_full_decode says its embedded preview is too small, else its embedded preview at native size is
used. A sidecar that will not open falls back to the raw rule; a demosaic that fails falls back to the preview.

EXIF carried (best effort, from the file the pixels came from, or from the primary for a raw): everything Pillow
reads from IFD0, the Exif sub-IFD and GPS, with Orientation forced to 1. Dropped on purpose: the embedded
thumbnail, the MakerNote and Interoperability pointer (their internal offsets do not survive a rewrite) and the
stored pixel dimensions (they describe the uncropped picture). A source whose EXIF will not read gives a file
whose only EXIF is Orientation 1, never a failure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from spuza import preview
from spuza.catalog import RAW_EXTS

if TYPE_CHECKING:  # spuza.ratio imports this module; the name is only an annotation here
    from spuza.ratio import Ratio

Crop = tuple[float, float, float, float]

MIN_SIDE = 0.01  # a crop narrower or shorter than this fraction of the picture is not a crop
JPEG_QUALITY = 95

_ORIENTATION = 274
_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825
_DROP_EXIF = (0x927C, 0xA005, 0xA002, 0xA003)  # MakerNote, Interop pointer, PixelXDimension, PixelYDimension
_DROP_IFD0 = (0x0100, 0x0101, 0x0201, 0x0202)  # stored size and thumbnail pointers
_TURN_OPS = {
    1: Image.Transpose.ROTATE_270,  # PIL's ROTATE_90 is counter-clockwise
    2: Image.Transpose.ROTATE_180,
    3: Image.Transpose.ROTATE_90,
}


class CropError(Exception):
    """A crop could not be rendered or written."""


@dataclass(frozen=True)
class CropJob:
    primary: Path
    sidecar: Path | None
    crop: Crop
    turns: int = 0
    ratio: Ratio | None = None  # the aspect ratio the crop was made at; None is Free (names the export, nothing else)


def _number(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def normalise(rect) -> Crop | None:
    """Clamp ``rect`` to [0, 1], order its corners; None for garbage or a side under MIN_SIDE."""
    if not isinstance(rect, (tuple, list)) or len(rect) != 4:
        return None
    vals = [_number(v) for v in rect]
    if any(v is None for v in vals):
        return None
    a, b, c, d = (min(1.0, max(0.0, v)) for v in vals)
    left, right = min(a, c), max(a, c)
    top, bottom = min(b, d), max(b, d)
    if right - left < MIN_SIDE or bottom - top < MIN_SIDE:
        return None
    return (left, top, right, bottom)


def crop_box(size: tuple[int, int], crop: Crop) -> tuple[int, int, int, int]:
    """The crop in pixels of a picture of ``size`` (width, height); at least 1 px each way, inside the picture."""
    w, h = size
    left, top, right, bottom = crop
    x0 = min(max(0, round(left * w)), max(0, w - 1))
    y0 = min(max(0, round(top * h)), max(0, h - 1))
    x1 = min(w, max(x0 + 1, round(right * w)))
    y1 = min(h, max(y0 + 1, round(bottom * h)))
    return x0, y0, x1, y1


def _pixels(primary: Path, sidecar: Path | None) -> tuple[Image.Image, Path]:
    """(best pixels, the file they came from)."""
    if sidecar is not None and preview.is_jpeg_sidecar(sidecar):
        try:
            return preview.sidecar_native(sidecar)[0], Path(sidecar)
        except preview.PreviewError:
            pass  # unreadable sidecar: the raw rule
    if primary.suffix.lower() not in RAW_EXTS:
        return preview.native_preview(primary), primary
    im, info = preview.load(primary, None)
    if preview.needs_full_decode(primary, info):
        try:
            return preview.full_decode(primary), primary
        except preview.PreviewError:
            pass  # keep the embedded preview
    return im, primary


def _carry_exif(src: Path) -> tuple[bytes | None, bytes | None]:
    """(EXIF bytes with Orientation 1, ICC profile) of ``src``; (None, None) when it has none or will not read."""
    try:
        with Image.open(src) as im:
            icc = im.info.get("icc_profile")
            ex = im.getexif()
            if not len(ex):
                return None, icc
            sub = ex.get_ifd(_EXIF_IFD)
            for tag in _DROP_EXIF:
                sub.pop(tag, None)
            for tag in _DROP_IFD0:
                ex.pop(tag, None)
            ex.get_ifd(_GPS_IFD)
            ex[_ORIENTATION] = 1
            return ex.tobytes(), icc
    except Exception:  # noqa: BLE001 - EXIF is a courtesy; the pixels are what matter
        return None, None


def best_pixels(primary, sidecar=None) -> Image.Image:
    """The picture's best pixels as RGB, EXIF-oriented and not turned (see the module docstring); ``info`` carries
    "exif" (Orientation 1) and, when the source had one, "icc_profile". Raises CropError, nothing else."""
    try:
        primary = Path(primary)
        sidecar = Path(sidecar) if sidecar is not None else None
        im, origin = _pixels(primary, sidecar)
        im = im.convert("RGB")
        exif, icc = _carry_exif(origin)
        if exif is None and origin != primary:
            exif, _ = _carry_exif(primary)
        if not exif:
            bare = Image.Exif()
            bare[_ORIENTATION] = 1
            exif = bare.tobytes()
        im.info["exif"] = exif
        if icc:
            im.info["icc_profile"] = icc
        return im
    except CropError:
        raise
    except Exception as e:  # noqa: BLE001 - one clean error for the copy to report
        raise CropError(f"{type(e).__name__}: {e}") from e


def render(primary, sidecar, crop, turns: int = 0) -> Image.Image:
    """The cropped picture, ``turns`` clockwise quarter turns baked in (see the module docstring)."""
    try:
        norm = normalise(crop)
        if norm is None:
            raise CropError(f"not a usable crop: {crop!r}")
        im = best_pixels(primary, sidecar)
        out = im.crop(crop_box(im.size, norm))
        op = _TURN_OPS.get(int(turns) % 4)
        if op is not None:
            out = out.transpose(op)
        out.info["exif"] = im.info["exif"]
        if im.info.get("icc_profile"):
            out.info["icc_profile"] = im.info["icc_profile"]
        return out
    except CropError:
        raise
    except Exception as e:  # noqa: BLE001 - one clean error for the copy to report
        raise CropError(f"{type(e).__name__}: {e}") from e


def write_jpeg(im: Image.Image, dst) -> None:
    """Save ``im`` to ``dst`` as a JPEG (quality 95) with the EXIF and profile ``render`` attached."""
    kw = {}
    if im.info.get("exif"):
        kw["exif"] = im.info["exif"]
    if im.info.get("icc_profile"):
        kw["icc_profile"] = im.info["icc_profile"]
    try:
        im.convert("RGB").save(dst, "JPEG", quality=JPEG_QUALITY, **kw)
    except Exception as e:  # noqa: BLE001
        raise CropError(f"{type(e).__name__}: {e}") from e
