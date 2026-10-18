"""The crop rectangle drawn over the big picture (friend round 5).

A child of the viewer's image item (`Viewer.image_item()`), so it shares the item's rotation and follows
the fitted view, the 100% view and the capped-preview -> full-decode pixmap swap. The rectangle is stored
as fractions (left, top, right, bottom) of the parent pixmap's own, unrotated frame; `sync()` re-reads
the pixmap size (call it from the viewer's `pixmap_changed`). Handles, border and hit sizes are in device
pixels (cosmetic pens, size from the item-to-viewport scale), so they look alike at any fit scale and under
90/180/270 degree rotation. Not editing and nothing applied: invisible and transparent to the mouse.
Never takes keyboard focus. Aspect is free.

API: begin(rect=None) / end() / editing / rect_frac() / set_rect_frac(rect) / show_applied(rect) / sync(),
signal `changed`.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsObject, QGraphicsPixmapItem

MIN_SIDE = 0.01  # a side under 1 % of the picture is no crop
HANDLE_PX = 9.0  # drawn handle size, device pixels
HIT_PX = 12.0  # grab radius around a handle, device pixels
_PAD = 100.0  # item units the bounding rect extends past the picture, so edge handles stay hittable
_DIM = QColor(0, 0, 0, 120)

_CURSORS = {
    (-1, -1): Qt.CursorShape.SizeFDiagCursor, (1, 1): Qt.CursorShape.SizeFDiagCursor,
    (1, -1): Qt.CursorShape.SizeBDiagCursor, (-1, 1): Qt.CursorShape.SizeBDiagCursor,
    (0, -1): Qt.CursorShape.SizeVerCursor, (0, 1): Qt.CursorShape.SizeVerCursor,
    (-1, 0): Qt.CursorShape.SizeHorCursor, (1, 0): Qt.CursorShape.SizeHorCursor,
}


def _clean(rect) -> tuple[float, float, float, float] | None:
    """Ordered, clamped to 0..1 fractions; None for anything that is not four finite numbers."""
    try:
        vals = [float(v) for v in rect]
    except (TypeError, ValueError):
        return None
    if len(vals) != 4 or not all(math.isfinite(v) for v in vals):
        return None
    left, top, right, bottom = (min(1.0, max(0.0, v)) for v in vals)
    return (min(left, right), min(top, bottom), max(left, right), max(top, bottom))


class CropOverlay(QGraphicsObject):
    changed = Signal()

    def __init__(self, parent_item: QGraphicsPixmapItem):
        super().__init__(parent_item)
        self._parent = parent_item
        self._editing = False
        self._rect: tuple[float, float, float, float] | None = None  # the editing rectangle, ordered
        self._applied: tuple[float, float, float, float] | None = None
        self._size = (0.0, 0.0)
        self._scale = 1.0  # item -> device scale, refreshed on paint and on mouse events
        self._mode: object = None  # None | "new" | "move" | (sx, sy) handle
        self._anchor = QPointF()
        self._start: tuple[float, float, float, float] = (0, 0, 0, 0)  # the rectangle at press
        self._dragging = False
        self.setZValue(9)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setVisible(False)
        self.sync()

    # ----- API -----
    @property
    def editing(self) -> bool:
        return self._editing

    def begin(self, rect=None) -> None:
        self._editing = True
        self._rect = _clean(rect) if rect is not None else None
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)
        self._refresh()

    def end(self) -> None:
        self._editing = False
        self._mode = None
        self._dragging = False
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.unsetCursor()
        self._refresh()

    def rect_frac(self) -> tuple[float, float, float, float] | None:
        r = self._rect
        if r is None or r[2] - r[0] < MIN_SIDE or r[3] - r[1] < MIN_SIDE:
            return None
        return r

    def set_rect_frac(self, rect) -> None:
        self._rect = None if rect is None else _clean(rect)
        self.update()

    def show_applied(self, rect) -> None:
        """Passive display of an applied crop (None hides it): a thin dashed outline, no mouse."""
        self._applied = None if rect is None else _clean(rect)
        self._refresh()

    def sync(self) -> None:
        """Follow the parent pixmap's current size (after a pixmap swap)."""
        pm = self._parent.pixmap()
        size = (float(pm.width()), float(pm.height()))
        if size != self._size:
            self.prepareGeometryChange()
            self._size = size
        self.update()

    # ----- geometry -----
    def boundingRect(self) -> QRectF:
        w, h = self._size
        return QRectF(-_PAD, -_PAD, w + 2 * _PAD, h + 2 * _PAD)

    def _refresh(self) -> None:
        self.sync()
        self.setVisible(self._editing or self._applied is not None)
        self.update()

    def _px(self, r) -> QRectF:
        w, h = self._size
        return QRectF(QPointF(r[0] * w, r[1] * h), QPointF(r[2] * w, r[3] * h))

    def _update_scale(self) -> None:
        sc = self.scene()
        views = sc.views() if sc is not None else []
        if views:
            det = abs((self.sceneTransform() * views[0].viewportTransform()).determinant())
            if det > 0:
                self._scale = math.sqrt(det)

    @staticmethod
    def _handles(r: QRectF) -> dict[tuple[int, int], QPointF]:
        xs = {-1: r.left(), 0: r.center().x(), 1: r.right()}
        ys = {-1: r.top(), 0: r.center().y(), 1: r.bottom()}
        return {(sx, sy): QPointF(xs[sx], ys[sy]) for sx in (-1, 0, 1) for sy in (-1, 0, 1) if (sx, sy) != (0, 0)}

    def _hit(self, pos: QPointF):
        """A handle key (sx, sy), "move", or None for a point (item coordinates)."""
        self._update_scale()
        if self._rect is None:
            return None
        rect = self._px(self._rect)
        best, best_d = None, HIT_PX / self._scale
        for key, pt in self._handles(rect).items():
            d = max(abs(pt.x() - pos.x()), abs(pt.y() - pos.y()))
            if d <= best_d:
                best, best_d = key, d
        if best is not None:
            return best
        return "move" if rect.contains(pos) else None

    # ----- painting -----
    def paint(self, painter: QPainter, option, widget=None) -> None:
        w, h = self._size
        if w <= 0 or h <= 0:
            return
        det = abs(painter.worldTransform().determinant())
        if det > 0:
            self._scale = math.sqrt(det)
        if not self._editing:
            if self._applied is not None:
                pen = QPen(QColor(255, 255, 255, 220), 1.0, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(self._px(self._applied))
            return
        if self._rect is None:
            return
        rect = self._px(self._rect)
        whole = QPainterPath()
        whole.addRect(QRectF(0, 0, w, h))
        hole = QPainterPath()
        hole.addRect(rect)
        painter.fillPath(whole.subtracted(hole), _DIM)
        pen = QPen(QColor(235, 235, 235), 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        if self._dragging:
            thirds = QPen(QColor(255, 255, 255, 110), 1.0)
            thirds.setCosmetic(True)
            painter.setPen(thirds)
            for i in (1, 2):
                x = rect.left() + rect.width() * i / 3
                y = rect.top() + rect.height() * i / 3
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        half = HANDLE_PX / 2 / self._scale
        painter.setPen(pen)
        painter.setBrush(QColor(20, 20, 20, 220))
        for pt in self._handles(rect).values():
            painter.drawRect(QRectF(pt.x() - half, pt.y() - half, 2 * half, 2 * half))

    # ----- mouse -----
    def _clamp_pt(self, pos: QPointF) -> tuple[float, float]:
        w, h = self._size
        return (min(1.0, max(0.0, pos.x() / w)) if w else 0.0, min(1.0, max(0.0, pos.y() / h)) if h else 0.0)

    def hoverMoveEvent(self, event) -> None:
        hit = self._hit(event.pos()) if self._editing else None
        if hit == "move":
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        elif hit is not None:
            self.setCursor(QCursor(_CURSORS[hit]))
        else:
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def mousePressEvent(self, event) -> None:
        if not self._editing or event.button() != Qt.MouseButton.LeftButton or self._size[0] <= 0:
            event.ignore()
            return
        hit = self._hit(event.pos())
        px, py = self._clamp_pt(event.pos())
        self._anchor = QPointF(px, py)
        if hit is None:
            self._mode = "new"
            self._rect = (px, py, px, py)
        else:
            self._mode = hit
            self._start = self._rect
        self._dragging = True
        event.accept()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            event.ignore()
            return
        px, py = self._clamp_pt(event.pos())
        if self._mode == "new":
            ax, ay = self._anchor.x(), self._anchor.y()
            self._rect = (min(ax, px), min(ay, py), max(ax, px), max(ay, py))
        elif self._mode == "move":
            left, top, right, bottom = self._start
            dx = min(1.0 - right, max(-left, px - self._anchor.x()))
            dy = min(1.0 - bottom, max(-top, py - self._anchor.y()))
            self._rect = (left + dx, top + dy, right + dx, bottom + dy)
        else:
            sx, sy = self._mode
            left, top, right, bottom = self._start
            if sx < 0:
                left = px
            elif sx > 0:
                right = px
            if sy < 0:
                top = py
            elif sy > 0:
                bottom = py
            self._rect = (min(left, right), min(top, bottom), max(left, right), max(top, bottom))
        self.update()
        self.changed.emit()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if not self._dragging:
            event.ignore()
            return
        self._dragging = False
        self._mode = None
        self.update()
        self.changed.emit()
        event.accept()
