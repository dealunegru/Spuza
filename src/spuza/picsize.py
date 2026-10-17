"""The PICTURE's own pixel size, as opposed to the size of the preview decoded for the screen.

`picture_size(pic, info)` returns ((w, h) | None, source). Sources (PreviewInfo.picture_source):

  jpeg-sidecar                  the .jpg/.jpeg beside a raw: size from its header, EXIF orientation applied
  raw-frame                     a raw with no JPEG beside it: the camera's frame (spuza.preview.frame_size)
  decoded                       anything else, or every read failed: the decoded preview's own size
  raw-frame, sidecar unreadable the pair's JPEG could not be opened, so the raw's frame is used
  decoded, sidecar unreadable   same, and the frame could not be read either

Runs on the prefetch worker only. Nothing here raises; a failed read degrades to the next rule.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from spuza.catalog import RAW_EXTS

JPEG_EXTS = frozenset({".jpg", ".jpeg"})
UNREADABLE = ", sidecar unreadable"


def jpeg_sidecar(pic) -> Path | None:
    """The pair's JPEG sidecar, or None (a .hif sidecar is not a JPEG)."""
    side = getattr(pic, "sidecar", None)
    if side is not None and Path(side).suffix.lower() in JPEG_EXTS:
        return Path(side)
    return None


def jpeg_size(path) -> tuple[int, int]:
    """Pixel size from the JPEG header (no decode), swapped for EXIF orientation 5-8. Raises on failure."""
    from PIL import Image

    with Image.open(path) as im:
        w, h = im.size
        try:
            orient = int(im.getexif().get(0x0112, 1))
        except Exception:  # noqa: BLE001 - a bad orientation tag means "not rotated"
            orient = 1
    return (h, w) if 5 <= orient <= 8 else (w, h)


def _decoded(info) -> tuple[int, int] | None:
    w, h = getattr(info, "width", None), getattr(info, "height", None)
    return (w, h) if isinstance(w, int) and isinstance(h, int) else None


def picture_size(pic, info) -> tuple[tuple[int, int] | None, str]:
    side = jpeg_sidecar(pic) if pic is not None else None
    unreadable = ""
    if side is not None:
        try:
            return jpeg_size(side), "jpeg-sidecar"
        except Exception:  # noqa: BLE001 - unreadable pair JPEG: fall back to the raw rule
            unreadable = UNREADABLE
    primary = getattr(pic, "primary", None) or getattr(info, "path", None)
    if primary is not None:
        from spuza.preview import frame_size  # lazy: rawpy stays out of module import

        frame = None
        try:
            frame = frame_size(primary)
        except Exception:  # noqa: BLE001
            frame = None
        if frame is not None:
            return tuple(frame), "raw-frame" + unreadable
    return _decoded(info), "decoded" + unreadable


def _can_carry(info) -> bool:
    return dataclasses.is_dataclass(info) and not isinstance(info, type) and all(
        hasattr(info, f) for f in ("picture_size", "picture_source"))


def _replace(info, size, source):
    return dataclasses.replace(info, picture_size=size, picture_source=source)


def quick_picture_size(pic, info):
    """Cheap half, safe before the first paint (Pillow header read only, no rawpy).

    Returns `info` with the size filled in when no rawpy is needed (a readable JPEG sidecar, or a primary
    that is not a raw); otherwise `info` unchanged, and `needs_frame(info)` is True."""
    if not _can_carry(info):
        return info
    try:
        side = jpeg_sidecar(pic) if pic is not None else None
        if side is not None:
            try:
                return _replace(info, jpeg_size(side), "jpeg-sidecar")
            except Exception:  # noqa: BLE001 - the raw rule decides, after the image is shown
                return info
        primary = getattr(pic, "primary", None) or getattr(info, "path", None)
        if primary is not None and Path(primary).suffix.lower() not in RAW_EXTS:
            return _replace(info, _decoded(info), "decoded")
    except Exception:  # noqa: BLE001 - never fail a load because the size read failed
        pass
    return info


def needs_frame(info) -> bool:
    """True when `quick_picture_size` left the size open: the slow half (`with_picture_size`) must run."""
    return _can_carry(info) and getattr(info, "picture_source", None) is None


def with_picture_size(pic, info):
    """`info` with picture_size/picture_source filled in (the full rule); `info` itself when it cannot carry them."""
    if not _can_carry(info):
        return info
    try:
        size, source = picture_size(pic, info)
        return _replace(info, size, source)
    except Exception:  # noqa: BLE001 - never fail a load because the size read failed
        return info
