"""Focus peaking: an edge mask of a decoded preview (flag focus_peaking, advanced-only).

No widgets here. `mask_image` is safe to call on a worker thread: it builds a QImage (not a QObject) from
numpy arrays, never a QPixmap. The cluster that owns this module replaces the stubs (spuza-features-r1, task
spz-nx-peak).
"""

from __future__ import annotations

from PySide6.QtGui import QImage

DEFAULT_COLOR = (255, 40, 40)  # the overlay's edge colour (RGB); alpha comes from the mask


def edge_mask(rgb, threshold: int, half: bool = True):
    """Boolean (H, W) numpy mask of the edges whose luminance gradient is at or above `threshold` (1-255).

    `rgb` is an (H, W, 3) uint8 array. With `half`, the gradient is computed on a half-size copy and the mask
    is returned at half size ((H+1)//2, (W+1)//2); the caller scales it up for display.
    """
    raise NotImplementedError


def mask_image(qimg: QImage, threshold: int, half: bool = True, color=DEFAULT_COLOR) -> QImage:
    """An ARGB32 overlay for `qimg`: `color` where the edge mask is set, fully transparent elsewhere.

    Its size is the mask's (half of `qimg` with `half`); draw it scaled to the picture. Worker-thread safe.
    """
    raise NotImplementedError
