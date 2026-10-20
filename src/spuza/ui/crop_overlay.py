"""The crop rectangle drawn over the big picture (friend round 5).

A child of the viewer's image item (`Viewer.image_item()`), so it shares the item's rotation and follows
the fitted view, the 100% view and the capped-preview -> full-decode pixmap swap. The rectangle is stored
as fractions (left, top, right, bottom) of the parent pixmap's own, unrotated frame; `sync()` re-reads
the pixmap size (call it from the viewer's `pixmap_changed`). Handles, border and hit sizes are in device
pixels (cosmetic pens, size from the item-to-viewport scale), so they look alike at any fit scale and under
90/180/270 degree rotation. Not editing and nothing applied: invisible and transparent to the mouse.
Never takes keyboard focus. Aspect is free until `set_aspect` locks it (round 6): then 4 corner handles, a corner
drag keeps the ratio with the opposite corner fixed, a drag on empty space draws a new rectangle at the ratio. While
editing, a label inside the rectangle ("4:5 · 3200 × 4000 px"; "Free · 3720 × 2480 px") is drawn upright on screen
whatever the picture's turn.

API: begin(rect=None) / end() / editing / rect_frac() / set_rect_frac(rect) / show_applied(rect) / sync() /
set_aspect(aspect_px_or_None, native_size, label="Free") / label_text(), signal `changed`.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsObject, QGraphicsPixmapItem

from spuza.ui import theme

MIN_SIDE = 0.01  # a side under 1 % of the picture is no crop
HANDLE_PX = 9.0  # drawn handle size, device pixels
HIT_PX = 12.0  # grab radius around a handle, device pixels
_PAD = 100.0  # item units the bounding rect extends past the picture, so edge handles stay hittable

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
        self._aspect: float | None = None  # locked: crop width / height in picture pixels; None = free
        self._native = (0, 0)  # the picture's own un-turned size, for the label's pixels and the fraction aspect
        self._label = "Free"
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

    def set_aspect(self, aspect, native, label: str = "Free") -> None:
        """Lock the rectangle to `aspect` (width / height in native pixels; None or not a positive number = free).
        `native` is the picture's un-turned (w, h); `label` is the ratio's on-screen name for the inside label."""
        ok = isinstance(aspect, (int, float)) and not isinstance(aspect, bool) and math.isfinite(aspect) and aspect > 0
        try:
            nw, nh = int(native[0]), int(native[1])
        except (TypeError, ValueError, IndexError, OverflowError):
            nw = nh = 0
        self._native = (nw, nh) if nw > 0 and nh > 0 else (0, 0)
        self._aspect = float(aspect) if ok and self._native != (0, 0) else None
        self._label = str(label) if label else "Free"
        self.update()

    @property
    def aspect(self) -> float | None:
        return self._aspect

    def _frac_aspect(self) -> float | None:
        """The locked aspect as a ratio of the rectangle's FRACTIONS (pixel aspect * h / w)."""
        if self._aspect is None:
            return None
        return self._aspect * self._native[1] / self._native[0]

    def label_text(self) -> str:
        """The text inside the rectangle: "<ratio label> · W × H px" (pixels of the native picture)."""
        r = self.rect_frac()
        if r is None or self._native == (0, 0):
            return self._label
        from spuza.ratio import pixel_size

        pw, ph = pixel_size(r, self._native[0], self._native[1])
        return f"{self._label} · {pw} × {ph} px"

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

    def _handles(self, r: QRectF) -> dict[tuple[int, int], QPointF]:
        xs = {-1: r.left(), 0: r.center().x(), 1: r.right()}
        ys = {-1: r.top(), 0: r.center().y(), 1: r.bottom()}
        locked = self._aspect is not None  # a locked ratio has the 4 corners only
        return {(sx, sy): QPointF(xs[sx], ys[sy]) for sx in (-1, 0, 1) for sy in (-1, 0, 1)
                if (sx, sy) != (0, 0) and not (locked and 0 in (sx, sy))}

    @staticmethod
    def _locked_rect(fixed: tuple[float, float], px: float, py: float, fa: float, sx: int = 1, sy: int = 1):
        """The rectangle of fraction aspect `fa` with corner `fixed` kept and the opposite corner as near the pointer
        as the ratio and the picture allow (the pointer picks the quadrant; (sx, sy) breaks a tie)."""
        fx, fy = fixed
        dirx = 1 if px > fx else (-1 if px < fx else (sx or 1))
        diry = 1 if py > fy else (-1 if py < fy else (sy or 1))
        w = max(abs(px - fx), abs(py - fy) * fa)
        w = min(w, (1.0 - fx) if dirx > 0 else fx, ((1.0 - fy) if diry > 0 else fy) * fa)
        h = w / fa
        x2, y2 = fx + dirx * w, fy + diry * h
        return (min(fx, x2), min(fy, y2), max(fx, x2), max(fy, y2))

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
                pen = QPen(theme.qcolor(theme.current().on_focus, 220), 1.0, Qt.PenStyle.DashLine)
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
        c = theme.current()
        painter.fillPath(whole.subtracted(hole), theme.qcolor(c.hud, 120))
        pen = QPen(theme.qcolor(c.on_focus, 235), 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        if self._dragging:
            thirds = QPen(theme.qcolor(c.on_focus, 110), 1.0)
            thirds.setCosmetic(True)
            painter.setPen(thirds)
            for i in (1, 2):
                x = rect.left() + rect.width() * i / 3
                y = rect.top() + rect.height() * i / 3
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        half = HANDLE_PX / 2 / self._scale
        painter.setPen(pen)
        painter.setBrush(theme.qcolor(c.hud, 220))
        for pt in self._handles(rect).values():
            painter.drawRect(QRectF(pt.x() - half, pt.y() - half, 2 * half, 2 * half))
        self._paint_label(painter, rect)

    def _paint_label(self, painter: QPainter, rect: QRectF) -> None:
        """The label as a pill at the rectangle's centre, upright on screen: the item's turn and scale are undone by
        painting in device coordinates at the mapped centre."""
        text = self.label_text()
        if not text or rect.width() <= 0 or rect.height() <= 0:
            return
        centre = painter.worldTransform().map(rect.center())
        painter.save()
        painter.resetTransform()
        f = QFont(painter.font())
        f.setPixelSize(13)
        f.setBold(True)
        painter.setFont(f)
        fm = painter.fontMetrics()
        tw, th = fm.horizontalAdvance(text) + 18, fm.height() + 8
        pill = QRectF(centre.x() - tw / 2, centre.y() - th / 2, tw, th)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.qcolor(theme.current().hud, 184))
        painter.drawRoundedRect(pill, th / 2, th / 2)
        painter.setPen(QColor(theme.current().on_focus))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

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
        fa = self._frac_aspect()
        if self._mode == "new":
            ax, ay = self._anchor.x(), self._anchor.y()
            if fa is None:
                self._rect = (min(ax, px), min(ay, py), max(ax, px), max(ay, py))
            else:
                self._rect = self._locked_rect((ax, ay), px, py, fa)
        elif self._mode == "move":
            left, top, right, bottom = self._start
            dx = min(1.0 - right, max(-left, px - self._anchor.x()))
            dy = min(1.0 - bottom, max(-top, py - self._anchor.y()))
            self._rect = (left + dx, top + dy, right + dx, bottom + dy)
        elif fa is not None:
            sx, sy = self._mode
            left, top, right, bottom = self._start
            # the opposite corner stays; the dragged corner follows the pointer at the ratio
            self._rect = self._locked_rect((right if sx < 0 else left, bottom if sy < 0 else top), px, py, fa, sx, sy)
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
