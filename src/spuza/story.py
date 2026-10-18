"""Story fit: a fixed 9:16 frame over a picture that moves and zooms under it (pure Python, no Qt).

The user's quarter turns apply first, so everything here works in the TURNED picture's frame, W x H. The frame
is Fw x Fh (9:16). With s = frame pixels per source pixel and the picture centre at frame centre + (ox, oy):

    fit_scale(W, H, Fw, Fh)    s_fit   = min(Fw/W, Fh/H)      the whole picture inside the frame
    cover_scale(W, H, Fw, Fh)  s_cover = max(Fw/W, Fh/H)      the frame filled edge to edge

`Story(zoom, dx, dy)` is the stored, resolution-independent state: zoom = s / s_fit (>= 1; 1 is "fit"),
dx = ox / Fw, dy = oy / Fh. The same Story gives the same picture on a 540x960 screen frame and on the
1080x1920 export frame.

Limits (all in EXPORT space, so they do not depend on the screen): the smallest scale is fit; the largest is
`max_scale(W, H) = max(s_cover, min(8 * s_cover, 1.0))`, where 1.0 is one frame pixel per source pixel in the
1080-wide export (a tiny picture therefore tops out at fill). On an axis where the scaled picture covers the
frame, |offset| <= (W*s - Fw)/2; on an axis where it does not cover, that axis is centred (offset 0).

API: FRAME_W, FRAME_H, EXPORT_SIZE, Story, fit_scale, cover_scale, max_scale, fill_story, clamp,
zoom_about, pan, zoom_step, visible_rect, render, save. Offsets taken in pixels (`p`, `pan`) are relative to
the frame centre, in pixels of the frame size given as `frame_size` (default: the export frame).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

FRAME_W, FRAME_H = 1080, 1920
EXPORT_SIZE = (FRAME_W, FRAME_H)
MAX_COVER_MULT = 8.0
STEP = 0.25  # +/- step, in frame pixels per source pixel in the export frame
JPEG_QUALITY = 92
_EPS = 1e-9


@dataclass(frozen=True)
class Story:
    zoom: float = 1.0
    dx: float = 0.0
    dy: float = 0.0


def fit_scale(w: float, h: float, fw: float = FRAME_W, fh: float = FRAME_H) -> float:
    return min(fw / w, fh / h)


def cover_scale(w: float, h: float, fw: float = FRAME_W, fh: float = FRAME_H) -> float:
    return max(fw / w, fh / h)


def max_scale(w: float, h: float) -> float:
    """The largest allowed scale, in export-frame pixels per source pixel."""
    cover = cover_scale(w, h)
    return max(cover, min(MAX_COVER_MULT * cover, 1.0))


def _max_zoom(w: float, h: float) -> float:
    return max_scale(w, h) / fit_scale(w, h)


def fill_story(w: float, h: float) -> Story:
    """Cover the whole frame, centred."""
    return Story(cover_scale(w, h) / fit_scale(w, h), 0.0, 0.0)


def _finite(x: float, default: float) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _clamp_zoom(zoom: float, w: float, h: float) -> float:
    return min(max(_finite(zoom, 1.0), 1.0), _max_zoom(w, h))


def _clamp_offset(d: float, extent: float, scale: float, frame: float) -> float:
    """`d` is the offset as a fraction of the frame; the picture's `extent` is in source pixels."""
    over = extent * scale / frame
    if over <= 1.0 + _EPS:
        return 0.0
    limit = (over - 1.0) / 2.0
    return min(max(_finite(d, 0.0), -limit), limit)


def clamp(story: Story, w: float, h: float) -> Story:
    """Bring a Story inside the limits for a turned picture of w x h (garbage in gives fit)."""
    if not (w > 0 and h > 0):
        return Story(1.0, 0.0, 0.0)
    zoom = _clamp_zoom(story.zoom, w, h)
    s = zoom * fit_scale(w, h)
    return Story(zoom, _clamp_offset(story.dx, w, s, FRAME_W), _clamp_offset(story.dy, h, s, FRAME_H))


def zoom_about(
    story: Story,
    w: float,
    h: float,
    p: tuple[float, float],
    *,
    factor: float | None = None,
    zoom: float | None = None,
    frame_size: tuple[float, float] = EXPORT_SIZE,
) -> Story:
    """Zoom (by `factor`, or to the absolute `zoom`) keeping the source point under `p` fixed, then clamp."""
    story = clamp(story, w, h)
    new = story.zoom * factor if zoom is None and factor is not None else zoom
    if new is None:
        return story
    new = _clamp_zoom(new, w, h)
    r = new / story.zoom
    fw, fh = frame_size
    px, py = _finite(p[0], 0.0), _finite(p[1], 0.0)
    dx = (px - (px - story.dx * fw) * r) / fw
    dy = (py - (py - story.dy * fh) * r) / fh
    return clamp(Story(new, dx, dy), w, h)


def pan(
    story: Story, w: float, h: float, ddx_px: float, ddy_px: float, frame_size: tuple[float, float] = EXPORT_SIZE
) -> Story:
    fw, fh = frame_size
    story = clamp(story, w, h)
    return clamp(Story(story.zoom, story.dx + _finite(ddx_px, 0.0) / fw, story.dy + _finite(ddy_px, 0.0) / fh), w, h)


def zoom_step(story: Story, w: float, h: float, direction: int) -> Story:
    """+/-: the next (or previous) multiple of 0.25 in export-frame scale, clamped to [fit, max]; about the centre."""
    story = clamp(story, w, h)
    fit = fit_scale(w, h)
    s = story.zoom * fit
    if direction > 0:
        target = (math.floor(s / STEP + _EPS) + 1) * STEP
    else:
        target = (math.ceil(s / STEP - _EPS) - 1) * STEP
    target = min(max(target, fit), max_scale(w, h))
    return zoom_about(story, w, h, (0.0, 0.0), zoom=target / fit)


def visible_rect(
    story: Story, w: float, h: float, frame_size: tuple[float, float] = EXPORT_SIZE
) -> tuple[float, float, float, float]:
    """The part of the turned picture inside the frame, as (x, y, width, height) in source pixels."""
    story = clamp(story, w, h)
    fw, fh = frame_size
    s = story.zoom * fit_scale(w, h, fw, fh)
    ox, oy = story.dx * fw, story.dy * fh
    x0, y0 = w / 2 - (fw / 2 + ox) / s, h / 2 - (fh / 2 + oy) / s
    x1, y1 = x0 + fw / s, y0 + fh / s
    x0, y0, x1, y1 = max(x0, 0.0), max(y0, 0.0), min(x1, float(w)), min(y1, float(h))
    return (x0, y0, max(x1 - x0, 0.0), max(y1 - y0, 0.0))


def _turn(im: Image.Image, turns: int) -> Image.Image:
    t = int(turns) % 4
    if t == 0:
        return im
    op = {1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_90}[t]
    return im.transpose(op)  # PIL rotates counter-clockwise: 270 is one clockwise quarter turn


def render(im: Image.Image, turns: int, story: Story) -> Image.Image:
    """The framed picture as exactly 1080x1920 RGB; `im` is EXIF-oriented but not yet turned."""
    im = _turn(im.convert("RGB"), turns)
    w, h = im.size
    out = Image.new("RGB", EXPORT_SIZE, (0, 0, 0))
    if w < 1 or h < 1:
        return out
    story = clamp(story, w, h)
    s = story.zoom * fit_scale(w, h)
    ox, oy = story.dx * FRAME_W, story.dy * FRAME_H
    vx, vy, vw, vh = visible_rect(story, w, h)
    if vw <= 0 or vh <= 0:
        return out
    left = FRAME_W / 2 + ox + (vx - w / 2) * s
    top = FRAME_H / 2 + oy + (vy - h / 2) * s
    dl, dt = round(left), round(top)
    dr, db = round(left + vw * s), round(top + vh * s)
    dl, dt, dr, db = max(dl, 0), max(dt, 0), min(dr, FRAME_W), min(db, FRAME_H)
    if dr <= dl or db <= dt:
        return out
    part = im.resize((dr - dl, db - dt), Image.Resampling.LANCZOS, box=(vx, vy, vx + vw, vy + vh))
    out.paste(part, (dl, dt))
    return out


def save(img: Image.Image, dst, exif=None) -> None:
    """JPEG q92, plain RGB (sRGB), Orientation dropped; other EXIF kept when `exif` (Image.Exif or bytes) is given."""
    img = img.convert("RGB")
    kwargs: dict = {"quality": JPEG_QUALITY, "optimize": True}
    if exif is not None:
        ex = Image.Exif()
        ex.load(exif if isinstance(exif, (bytes, bytearray)) else exif.tobytes())
        ex.pop(0x0112, None)
        try:  # the stale pixel size of the source
            sub = ex.get_ifd(0x8769)
            sub.pop(0xA002, None)
            sub.pop(0xA003, None)
        except Exception:  # noqa: BLE001 - a malformed sub-IFD is not worth failing the export
            pass
        kwargs["exif"] = ex.tobytes()
    img.save(Path(dst), "JPEG", **kwargs)
