"""Aspect-ratio crop model: which ratio is chosen, what it means in pixels, how a crop changes shape. No widgets.

A ratio is ``Ratio(entry, turned)``. ``entry`` is the digit key 1..8; ``turned`` says the ratio is flipped relative to
how its entry opens (4:5 -> 5:4). Aspects here are width/height of the crop in the UN-turned native frame the crops of
``spuza.crop`` are measured in; ``turns`` (clockwise quarter turns, odd ones swap width and height) only matters where
text is shown or a ratio is opened "on screen".

API
    Ratio(entry, turned=False), FREE        frozen; Free is Ratio(1)
    ENTRIES                                 ((1, "Free"), (2, "Original"), (3, "1:1"), (4, "4:5"), (5, "3:4"),
                                            (6, "2:3"), (7, "9:16"), (8, "1.91:1")); 1.91:1 is 1080:566
    name(entry) -> str
    frame_aspect(r, w, h) -> float | None   width/height in un-turned pixels of a w x h picture; None for Free
    opening(entry, w, h, turns=0) -> Ratio  the ratio a digit press opens: 4-7 portrait and 8 landscape ON SCREEN
    flip(r) -> Ratio                        toggle ``turned``; Free and 1:1 come back unchanged
    label(r, turns=0) -> str                on-screen text, "4:5" / "5:4", "1.91:1" / "1:1.91", "Original", "Free"
    export_suffix(r, turns=0) -> str        "" for Free and Original, else on-screen "_4x5", "_191x100", ...
    encode(r) -> str, decode(v) -> Ratio | None   session round trip; decode never raises
    largest_centred(aspect, w, h) -> Crop   largest centred rect of that pixel aspect, snapped to whole pixels
    reshape(crop, aspect, w, h) -> Crop     same centre and pixel area at a new aspect, shrunk only as far as the
                                            picture forces, slid inside; the result always passes crop.normalise
    pixel_size(crop, w, h) -> (int, int)    the crop in native pixels (via crop.crop_box)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from spuza.crop import Crop, crop_box, normalise

ENTRIES: tuple[tuple[int, str], ...] = (
    (1, "Free"),
    (2, "Original"),
    (3, "1:1"),
    (4, "4:5"),
    (5, "3:4"),
    (6, "2:3"),
    (7, "9:16"),
    (8, "1.91:1"),
)

_NAMES = dict(ENTRIES)
_SIDES = {3: (1, 1), 4: (4, 5), 5: (3, 4), 6: (2, 3), 7: (9, 16), 8: (1080, 566)}
_LABEL_SIDES = {3: (1, 1), 4: (4, 5), 5: (3, 4), 6: (2, 3), 7: (9, 16), 8: ("1.91", "1")}
_SUFFIX_SIDES = {3: (1, 1), 4: (4, 5), 5: (3, 4), 6: (2, 3), 7: (9, 16), 8: (191, 100)}
_PORTRAIT = (4, 5, 6, 7)
_FULL: Crop = (0.0, 0.0, 1.0, 1.0)


@dataclass(frozen=True)
class Ratio:
    entry: int
    turned: bool = False


FREE = Ratio(1)


def _real(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        v = float(v)
    except OverflowError:
        return None
    return v if math.isfinite(v) else None


def _size(w, h) -> tuple[int, int] | None:
    a, b = _real(w), _real(h)
    if a is None or b is None or a < 1 or b < 1:
        return None
    return int(a), int(b)


def _aspect(v) -> float | None:
    a = _real(v)
    return a if a is not None and a > 0 else None


def name(entry: int) -> str:
    return _NAMES.get(entry, "Free")


def _effective(r: Ratio, turns: int) -> bool:
    """Is the ratio flipped as seen on screen (its own flip xor an odd number of quarter turns)?"""
    return bool(r.turned) != (int(turns) % 2 == 1)


def frame_aspect(r: Ratio, w, h) -> float | None:
    if r.entry == 2:
        size = _size(w, h)
        if size is None:
            return None
        a = size[0] / size[1]
    elif r.entry in _SIDES:
        a = _SIDES[r.entry][0] / _SIDES[r.entry][1]
    else:
        return None
    return 1 / a if r.turned and r.entry != 3 else a


def opening(entry: int, w, h, turns: int = 0) -> Ratio:
    if entry not in _NAMES or entry in (1, 2, 3):
        return Ratio(entry if entry in _NAMES else 1)
    # on screen 4-7 are portrait (as stored) and 8 landscape (as stored); odd turns flip the stored frame
    return Ratio(entry, int(turns) % 2 == 1)


def flip(r: Ratio) -> Ratio:
    if r.entry in (1, 3) or r.entry not in _NAMES:
        return r
    return Ratio(r.entry, not r.turned)


def label(r: Ratio, turns: int = 0) -> str:
    if r.entry not in _LABEL_SIDES:
        return name(r.entry)
    a, b = _LABEL_SIDES[r.entry]
    if r.entry != 3 and _effective(r, turns):
        a, b = b, a
    return f"{a}:{b}"


def export_suffix(r: Ratio, turns: int = 0) -> str:
    if r.entry not in _SUFFIX_SIDES:
        return ""
    a, b = _SUFFIX_SIDES[r.entry]
    if r.entry != 3 and _effective(r, turns):
        a, b = b, a
    return f"_{a}x{b}"


def encode(r: Ratio) -> str:
    return f"{r.entry}{'t' if r.turned else ''}"


def decode(v) -> Ratio | None:
    if not isinstance(v, str) or len(v) not in (1, 2):
        return None
    if v[0] not in "12345678" or v[1:] not in ("", "t"):
        return None
    return flip(Ratio(int(v[0]))) if v[1:] == "t" else Ratio(int(v[0]))


def largest_centred(aspect, w, h) -> Crop:
    a = _aspect(aspect)
    size = _size(w, h)
    if a is None or size is None:
        return _FULL
    w, h = size
    if w / h >= a:
        cw, ch = min(w, max(1, round(a * h))), h
    else:
        cw, ch = w, min(h, max(1, round(w / a)))
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    box = normalise((x0 / w, y0 / h, (x0 + cw) / w, (y0 + ch) / h))
    return box if box is not None else _FULL


def reshape(crop, aspect, w, h) -> Crop:
    a = _aspect(aspect)
    norm = normalise(crop)
    size = _size(w, h)
    if norm is None:
        return largest_centred(aspect, w, h)
    if a is None or size is None:
        return norm
    pw, ph = size
    cx, cy = (norm[0] + norm[2]) / 2, (norm[1] + norm[3]) / 2
    area = (norm[2] - norm[0]) * pw * (norm[3] - norm[1]) * ph
    fw, fh = math.sqrt(area * a) / pw, math.sqrt(area / a) / ph
    if fw > 1 or fh > 1:
        s = 1 / max(fw, fh)
        fw, fh = fw * s, fh * s
    fw, fh = (1.0 if v > 1 - 1e-9 else v for v in (fw, fh))
    left = min(max(cx - fw / 2, 0.0), 1 - fw)
    top = min(max(cy - fh / 2, 0.0), 1 - fh)
    out = normalise((left, top, left + fw, top + fh))
    return out if out is not None else largest_centred(aspect, w, h)


def pixel_size(crop: Crop, w, h) -> tuple[int, int]:
    x0, y0, x1, y1 = crop_box((int(w), int(h)), crop)
    return x1 - x0, y1 - y0
