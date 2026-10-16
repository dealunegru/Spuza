"""The thin ring at the camera's AF point on the big picture (flag focus_point, advanced-only).

Contract for the window (wired by spz-nx-wire): build one `FocusMarker(viewer)`; `set_on(...)` when O toggles;
`set_point(frac)` after each picture is shown, with `PreviewInfo.focus.frac()` or None. The ring is a child of
`viewer.image_item()`, so it follows the fitted view, the 100% view and the full-resolution swap. Its size is
constant on screen (ItemIgnoresTransformations); its place is the fraction of the item's CURRENT pixmap, re-set
whenever the viewer swaps the pixmap. Owned by task spz-nx-focus.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem

from spuza import features

RADIUS = 18.0  # screen pixels


class FocusMarker:
    def __init__(self, viewer):
        self._viewer = viewer
        self._on = False
        self._frac: tuple[float, float] | None = None
        self._ring = self._make(viewer.image_item(), QColor(0, 0, 0, 200), 3.5)
        self._inner = self._make(self._ring, QColor(255, 214, 10), 1.5)
        self._ring.setVisible(False)
        self._ring.setZValue(10)
        viewer.pixmap_changed.connect(self._place)

    @staticmethod
    def _make(parent, color: QColor, width: float) -> QGraphicsEllipseItem:
        r = RADIUS
        item = QGraphicsEllipseItem(QRectF(-r, -r, 2 * r, 2 * r), parent)
        pen = QPen(color, width)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        return item

    def is_on(self) -> bool:
        return self._on

    def set_on(self, on: bool) -> None:
        """No-op unless features.enabled("focus_point")."""
        if not features.enabled("focus_point"):
            return
        self._on = bool(on)
        self._place()

    def set_point(self, frac: tuple[float, float] | None) -> None:
        """Where the ring goes, as a fraction of the displayed picture; None hides it."""
        self._frac = None if frac is None else (float(frac[0]), float(frac[1]))
        self._place()

    def visible(self) -> bool:
        """The ring is on screen (on, and a point is set)."""
        return self._on and self._frac is not None and self._ring.isVisible()

    def _place(self, *_args) -> None:
        pm = self._viewer.image_item().pixmap()
        show = self._on and self._frac is not None and not pm.isNull()
        if show:
            self._ring.setPos(self._frac[0] * pm.width(), self._frac[1] * pm.height())
        self._ring.setVisible(show)
