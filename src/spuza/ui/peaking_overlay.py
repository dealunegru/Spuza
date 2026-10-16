"""The peaking overlay on the big picture (flag focus_peaking, advanced-only).

Contract for the window (wired by spz-nx-wire): build one `PeakingOverlay(viewer)`; call `set_on(...)` when P
toggles; call `show(index, qimg)` after each `viewer.show_image(index, ...)` with the cached preview. The mask is
computed on this object's own low-priority single-thread pool, never on the GUI thread and never ahead of decodes;
until it is ready the plain picture shows, and a step is never held back. Owned by task spz-nx-peak.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage


class PeakingOverlay(QObject):
    ready = Signal(int)  # the mask for picture `index` is on screen

    def __init__(self, viewer, threshold: int = 60, half: bool = True, parent=None):
        super().__init__(parent)
        raise NotImplementedError

    def is_on(self) -> bool:
        raise NotImplementedError

    def set_on(self, on: bool) -> None:
        """Show (and compute) masks, or hide the overlay and stop computing. Saving is the caller's business."""
        raise NotImplementedError

    def set_threshold(self, n: int) -> None:
        """New threshold (1-255): cached masks are dropped and the current one recomputed when on."""
        raise NotImplementedError

    def show(self, index: int, qimg: QImage | None) -> None:
        """The viewer now shows picture `index` from preview `qimg` (None: nothing to peak). No-op while off."""
        raise NotImplementedError

    def mask_for(self, index: int) -> QImage | None:
        """The finished overlay image for `index`, if cached (for compare cells and tests)."""
        raise NotImplementedError

    def shutdown(self, timeout_ms: int = 3000) -> None:
        raise NotImplementedError
