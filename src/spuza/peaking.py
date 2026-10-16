"""Focus peaking: an edge mask of a decoded preview (flag focus_peaking, advanced-only).

No widgets here. `mask_image` is safe to call on a worker thread: it builds a QImage (not a QObject) from
numpy arrays, never a QPixmap.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage

DEFAULT_COLOR = (255, 40, 40)  # the overlay's edge colour (RGB); alpha comes from the mask
ALPHA = 200  # opacity of an edge pixel in the overlay


def qimage_gray(qimg: QImage):
    """An (H, W) uint8 luminance copy of `qimg` (Qt's own conversion). Worker-thread safe."""
    im = qimg.convertToFormat(QImage.Format.Format_Grayscale8)
    w, h, bpl = im.width(), im.height(), im.bytesPerLine()
    if w == 0 or h == 0:
        return np.zeros((h, w), np.uint8)
    buf = np.frombuffer(im.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)
    return buf[:, :w].copy()


def _luma(rgb):
    a = np.asarray(rgb)
    if a.ndim != 3 or a.shape[2] < 3:
        raise ValueError("rgb must be an (H, W, 3) array")
    r, g, b = (a[..., i].astype(np.uint16) for i in range(3))
    return (r * 77 + g * 150 + b * 29) >> 8


def _halve(y):
    h, w = y.shape
    if h % 2 or w % 2:
        y = np.pad(y, ((0, h % 2), (0, w % 2)), mode="edge")
    return (y[0::2, 0::2] + y[1::2, 0::2] + y[0::2, 1::2] + y[1::2, 1::2]) >> 2


def edge_mask(rgb, threshold: int, half: bool = True):
    """Boolean (H, W) numpy mask of the edges whose luminance gradient is at or above `threshold` (1-255).

    `rgb` is an (H, W, 3) uint8 array. With `half`, the gradient is computed on a half-size copy and the mask
    is returned at half size ((H+1)//2, (W+1)//2); the caller scales it up for display.

    The gradient is Sobel's |gx|/4 + |gy|/4 on 8-bit luminance (integer shift). The one-pixel border is never set.
    """
    return _edges(_luma(rgb), threshold, half)


def _edges(y, threshold: int, half: bool):
    """edge_mask on an (H, W) uint8/uint16 luminance array."""
    y = y.astype(np.int16)
    if half:
        y = _halve(y)
    out = np.zeros(y.shape, bool)
    h, w = y.shape
    if h < 3 or w < 3:
        return out
    t = max(1, min(255, int(threshold)))
    top, mid, bot = y[:-2], y[1:-1], y[2:]
    gx = (top[:, 2:] - top[:, :-2]) + 2 * (mid[:, 2:] - mid[:, :-2]) + (bot[:, 2:] - bot[:, :-2])
    gy = (bot[:, :-2] - top[:, :-2]) + 2 * (bot[:, 1:-1] - top[:, 1:-1]) + (bot[:, 2:] - top[:, 2:])
    g = (np.abs(gx) + np.abs(gy)) >> 2
    out[1:-1, 1:-1] = g >= t
    return out


def mask_image(qimg: QImage, threshold: int, half: bool = True, color=DEFAULT_COLOR) -> QImage:
    """An ARGB32 overlay for `qimg`: `color` where the edge mask is set, fully transparent elsewhere.

    Its size is the mask's (half of `qimg` with `half`); draw it scaled to the picture. Worker-thread safe.
    """
    m = _edges(qimage_gray(qimg), threshold, half)
    h, w = m.shape
    r, g, b = (int(c) & 255 for c in color)
    px = np.zeros((h, w, 4), np.uint8)  # B, G, R, A: ARGB32 on a little-endian machine
    px[m] = (b, g, r, ALPHA)
    return QImage(px.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
