"""The story-fit editor (9:16): a fixed frame centred over the viewer, the photo moves and zooms under it.

`StoryEditor` is a child of the viewer's viewport and covers it while active. It paints the turned pixmap itself
(per the model in `spuza.story`), the largest 9:16 frame that fits with a margin, everything outside the frame
darkened, and a thin border; inside the frame, where the photo does not cover, it is black (what the export shows).

Mouse: wheel zooms about the cursor (one notch is a factor of 1.1), left-drag pans, double-click toggles fit/fill.
Keys that reach the widget (the window owns focus and forwards them; this widget never takes focus):
Enter/Return -> `applied`, Esc -> `cancelled`, + or = / - -> zoom_step(+1/-1), 0 -> reset(). Each is also a
public method. The pixmap may be a capped preview: the model works in fractions of the frame, so only its
aspect matters. No timers, no threads.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QWidget

from .. import story as st

MARGIN = 24
WHEEL_FACTOR = 1.1  # per 120 units of wheel angle
OUTSIDE = QColor(0, 0, 0, 165)
BACKDROP = QColor(18, 18, 18)
BORDER = QColor(255, 255, 255, 200)


class StoryEditor(QWidget):
    changed = Signal()
    applied = Signal()
    cancelled = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._pm: QPixmap | None = None
        self._src = (0, 0)
        self._story = st.Story()
        self._active = False
        self._last: QPointF | None = None  # drag anchor while the left button is down
        self.hide()
        parent.installEventFilter(self)

    # -- state ---------------------------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    def story(self) -> st.Story:
        return self._story

    def begin(
        self, pixmap: QPixmap, turns: int, story: st.Story | None, source_size: tuple[int, int] | None = None
    ) -> None:
        """`source_size` is the EXIF-oriented, un-turned native size; the pixmap may be a smaller preview."""
        t = int(turns) % 4
        self._pm = pixmap if t == 0 else pixmap.transformed(QTransform().rotate(90 * t))
        sw, sh = source_size if source_size else (pixmap.width(), pixmap.height())
        self._src = (sh, sw) if t % 2 else (sw, sh)  # the turned source size: every model call uses it
        self._story = self._clamped(story if story is not None else st.Story())
        self._active = True
        self._last = None
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.update()

    def end(self) -> None:
        self._active = False
        self._last = None
        self._pm = None
        self.hide()

    def eventFilter(self, obj, event):  # noqa: N802 - keep covering the viewport when it resizes
        if self._active and obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
        return False

    # -- model helpers -------------------------------------------------------------------------------------

    def _size(self) -> tuple[int, int]:
        return self._src if self._pm is not None else (0, 0)

    def _clamped(self, s: st.Story) -> st.Story:
        w, h = self._size()
        return st.clamp(s, w, h)

    def _frame(self) -> QRectF:
        aw, ah = max(self.width() - 2 * MARGIN, 1), max(self.height() - 2 * MARGIN, 1)
        fh = min(ah, aw * st.FRAME_H / st.FRAME_W)
        fw = fh * st.FRAME_W / st.FRAME_H
        return QRectF((self.width() - fw) / 2, (self.height() - fh) / 2, fw, fh)

    def _set(self, s: st.Story) -> None:
        if s != self._story:
            self._story = s
            self.update()
            self.changed.emit()

    # -- public actions (the window forwards its keys to these) --------------------------------------------

    def zoom_step(self, direction: int) -> None:
        if self._pm is None:
            return
        w, h = self._size()
        self._set(st.zoom_step(self._story, w, h, 1 if direction > 0 else -1))

    def reset(self) -> None:
        if self._pm is not None:
            self._set(st.Story())

    def toggle_fill(self) -> None:
        if self._pm is None:
            return
        w, h = self._size()
        fill = st.fill_story(w, h)
        if abs(self._story.zoom - fill.zoom) < 1e-6 and self._story.zoom > 1.0 + 1e-6:
            self._set(st.Story())
        else:
            self._set(fill)

    def apply(self) -> None:
        self.applied.emit()

    def cancel(self) -> None:
        self.cancelled.emit()

    # -- events --------------------------------------------------------------------------------------------

    def wheelEvent(self, e) -> None:  # noqa: N802
        if self._pm is None:
            e.ignore()
            return
        delta = e.angleDelta().y()
        if delta:
            fr = self._frame()
            pos = e.position()
            w, h = self._size()
            p = (pos.x() - fr.center().x(), pos.y() - fr.center().y())
            self._set(
                st.zoom_about(
                    self._story, w, h, p, factor=WHEEL_FACTOR ** (delta / 120.0), frame_size=(fr.width(), fr.height())
                )
            )
        e.accept()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._last = e.position()
        e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._last is not None and self._pm is not None:
            pos = e.position()
            fr = self._frame()
            w, h = self._size()
            d = pos - self._last
            self._last = pos
            self._set(st.pan(self._story, w, h, d.x(), d.y(), (fr.width(), fr.height())))
        e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._last = None
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self._last = None
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle_fill()
        e.accept()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        k = e.key()
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.apply()
        elif k == Qt.Key.Key_Escape:
            self.cancel()
        elif k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_step(1)
        elif k == Qt.Key.Key_Minus:
            self.zoom_step(-1)
        elif k == Qt.Key.Key_0:
            self.reset()
        else:
            e.ignore()
            return
        e.accept()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), BACKDROP)
        fr = self._frame()
        p.fillRect(fr, Qt.GlobalColor.black)
        if self._pm is not None:
            w, h = self._size()
            s = self._story.zoom * st.fit_scale(w, h, fr.width(), fr.height())
            cx = fr.center().x() + self._story.dx * fr.width()
            cy = fr.center().y() + self._story.dy * fr.height()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawPixmap(QRectF(cx - w * s / 2, cy - h * s / 2, w * s, h * s), self._pm, QRectF(self._pm.rect()))
        outside = QPainterPath()
        outside.addRect(QRectF(self.rect()))
        inner = QPainterPath()
        inner.addRect(fr)
        p.fillPath(outside.subtracted(inner), OUTSIDE)
        pen = QPen(BORDER)
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(fr)
        p.end()
