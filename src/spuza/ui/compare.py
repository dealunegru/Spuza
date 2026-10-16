"""Compare: up to four near-twins side by side, zoom and pan locked together (flag compare_view).

Contract for the window (wired by spz-nx-wire). The window adds one `CompareView` as a page of its stack and
calls `open(indices, focus)`; the view owns its keys while shown:
  ← → J K  move focus between cells (never off the set)    Space M  mark/unmark the focused cell
  Z        all cells to 100% of each embedded JPEG at the same fractional point; drag pans all
  N        2 -> 3 -> 4 cells when opened on a run from the single view; on 4 (or a grid pick) it closes
  Esc      closes
Fitted cells show the prefetcher's cached QImages (no decode); a cell outside the cache, and every zoomed cell,
decodes on the view's own worker pool. Owned by task spz-nx-compare.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

MAX_CELLS = 4


class CompareView(QWidget):
    closed = Signal(int)  # the catalog index focused when compare closed
    marks_changed = Signal()  # a mark was toggled from compare
    focus_changed = Signal(int)  # the focused cell now shows catalog index

    def __init__(self, catalog, prefetcher, parent=None, loader=None):
        """`loader(path, max_side)` -> (PIL image, PreviewInfo), default `spuza.preview.load`; tests inject one."""
        super().__init__(parent)
        raise NotImplementedError

    def open(self, indices: list[int], focus: int = 0, run: bool = True) -> None:
        """Show these catalog indices (1-4; more are cut to 4). `run`: opened on a run from the single view, so N
        grows the set with the following pictures; False for a review-grid pick, where N closes."""
        raise NotImplementedError

    def indices(self) -> list[int]:
        raise NotImplementedError

    def focused_index(self) -> int:
        raise NotImplementedError

    def is_zoomed(self) -> bool:
        raise NotImplementedError

    def set_advance_on_mark(self, on: bool) -> None:
        """Move on mark: marking moves focus to the next cell, never outside the set."""
        raise NotImplementedError

    def set_overlay(self, index: int, img: QImage | None) -> None:
        """Draw `img` (a peaking mask, any size) scaled over the fitted cell showing `index`; None removes it."""
        raise NotImplementedError

    def status_text(self) -> str:
        """The one-line status, e.g. "compare 3 · 2 marked"."""
        raise NotImplementedError

    def close_compare(self) -> None:
        """Leave compare: frees zoomed decodes and emits closed(focused_index())."""
        raise NotImplementedError

    def shutdown(self, timeout_ms: int = 3000) -> None:
        raise NotImplementedError
