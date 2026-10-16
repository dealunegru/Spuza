"""The big picture: a QGraphicsView on the raster backend (never OpenGL).

Fit-to-window by default; Z toggles 1:1 at the centre and the state carries to the next picture. A raw
whose embedded preview is much smaller than the sensor (dec-044) is demosaiced on a worker thread when
you zoom; the result replaces the preview only if that picture is still on screen.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from PySide6.QtCore import QPointF, QTimer, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
                               QLabel, QWidget)

from spuza.reveal import reveal_menu
from spuza.ui import theme

FULL_CACHE = 3  # full-resolution decodes kept (a 24 MP image is ~70 MB as a pixmap)


def _default_needs_full_decode(path, info) -> bool:
    from spuza.preview import needs_full_decode  # lazy: keeps rawpy out of import time

    return needs_full_decode(path, info)


def _default_full_decode(path, half_size: bool = False):
    from spuza.preview import full_decode

    return full_decode(path, half_size)


def to_qimage(im) -> QImage:
    rgb = im.convert("RGB")
    w, h = rgb.size
    return QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class _FullJob(QRunnable):
    def __init__(self, viewer: Viewer, index: int, path, info):
        super().__init__()
        self.viewer, self.index, self.path, self.info = viewer, index, path, info
        self.setAutoDelete(True)

    def _emit(self, *args) -> None:
        try:
            self.viewer._full_result.emit(*args)
        except RuntimeError:  # the viewer was destroyed while we decoded
            pass

    def run(self) -> None:
        v, t0 = self.viewer, time.perf_counter()
        try:
            if v.index != self.index:  # the user moved on while this waited in the queue
                self._emit(self.index, str(self.path), None, 0.0, "stale")
                return
            if self.info is None or not v.needs_full_decode(self.path, self.info):
                self._emit(self.index, str(self.path), None, 0.0, "skipped")
                return
            qimg = to_qimage(v.full_decode(self.path, False))
        except Exception as exc:  # noqa: BLE001 - reported to the HUD, never raised on a worker
            self._emit(self.index, str(self.path), None, 0.0, str(exc) or type(exc).__name__)
            return
        self._emit(self.index, str(self.path), qimg, (time.perf_counter() - t0) * 1000, "done")


class BleedStrip(QFrame):
    """The thin translucent bottom strip of full-bleed mode."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("bleedStrip")
        self.setStyleSheet("#bleedStrip { background: rgba(0,0,0,150); } QLabel { color: #f2f2f4; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        self.dot, self.pos, self.name, self.count = QLabel("●"), QLabel(), QLabel(), QLabel()
        for w in (self.dot, self.pos, self.name):
            lay.addWidget(w)
        lay.addStretch(1)
        self.note = QLabel()
        self.note.setStyleSheet("color: #ffd9a0; font-weight: 600;")
        self.note.hide()
        lay.addWidget(self.note)
        lay.addSpacing(16)
        lay.addWidget(self.count)
        self._note_timer = QTimer(self)  # single shot, one per message
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(self.expire_note)
        self.marked = False
        self.hide()

    def set_state(self, pos: str, marked: bool, name: str, kind: str, marked_count: int) -> None:
        c = theme.current()
        self.marked = marked
        self.dot.setStyleSheet(f"color: {c.green if marked else '#8a8a90'}; font-size: 16px;")
        self.pos.setText(pos)
        self.name.setText(f"{name}  [{kind}]" if name else "")
        self.count.setText(f"{marked_count} marked")

    def show_note(self, text: str, ms: int = 5000) -> None:
        self.note.setText(text)
        self.note.show()
        self._note_timer.start(ms)

    def expire_note(self) -> None:
        self._note_timer.stop()
        self.note.hide()

    def text(self) -> str:
        return f"{self.pos.text()}  {self.name.text()}  {self.count.text()}"


class Viewer(QGraphicsView):
    shown = Signal(int)  # a picture's pixmap was set
    painted = Signal(int)  # ... and the viewport painted it
    zoom_changed = Signal(bool)
    full_done = Signal(int, float)
    full_skipped = Signal(int)
    full_failed = Signal(int, str)
    pixmap_changed = Signal(int)  # the item's pixmap was replaced (preview, full-res swap, or cleared)
    _full_result = Signal(int, str, object, float, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.needs_full_decode = _default_needs_full_decode
        self.full_decode = _default_full_decode
        self.index = -1
        self.pic = None
        self.info = None
        self.zoomed = False
        self.error_text = ""
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._preview: QImage | None = None
        self._full_cache: OrderedDict[str, QImage] = OrderedDict()
        self._full: dict[int, str] = {}  # index -> pending | done | skipped | error
        self._paint_pending: int | None = None
        # Where Z lands: a callable returning the (fx, fy) fraction of the image to centre on, or None for
        # the centre. Set by the window (focus_point); None keeps the shipped behaviour.
        self.zoom_target = None
        self._scene = QGraphicsScene(self)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        vp = self.viewport()
        self.message = QLabel(vp)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.hide()
        self.hud = QLabel("decoding full resolution…", vp)
        self.hud.setStyleSheet("background: rgba(0,0,0,170); color: #fff; padding: 4px 10px;"
                               " border-radius: 9px;")
        self.hud.adjustSize()
        self.hud.hide()
        self.strip = BleedStrip(vp)
        self._full_result.connect(self._on_full)
        self.retheme()

    # -- state ------------------------------------------------------------------------------------
    @property
    def has_image(self) -> bool:
        return not self._item.pixmap().isNull()

    @property
    def hud_visible(self) -> bool:
        return not self.hud.isHidden()

    def image_size(self) -> tuple[int, int] | None:
        pm = self._item.pixmap()
        return None if pm.isNull() else (pm.width(), pm.height())

    def image_item(self) -> QGraphicsPixmapItem:
        """The picture's item; overlays parent to it so they share its transform."""
        return self._item

    def full_state(self, index: int) -> str | None:
        return self._full.get(index)

    # -- showing pictures -------------------------------------------------------------------------
    def _set_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self._place()

    def _clear(self, index: int, pic) -> None:
        self.index, self.pic, self.info, self._preview = index, pic, None, None
        self._item.setPixmap(QPixmap())
        self.pixmap_changed.emit(index)
        self._paint_pending = None
        self._sync_hud()

    def show_placeholder(self, index: int, pic) -> None:
        self._clear(index, pic)
        self.error_text = ""
        self._set_message("Loading…")

    def show_error(self, index: int, pic, reason: str) -> None:
        self._clear(index, pic)
        self.error_text = reason
        self._set_message(f"Cannot show {pic.primary.name}\n{reason}" if pic else reason)

    def show_message(self, text: str) -> None:
        self._clear(-1, None)
        self.error_text = text
        self._set_message(text)

    def show_image(self, index: int, pic, qimg: QImage, info) -> None:
        self.index, self.pic, self.info, self._preview = index, pic, info, qimg
        self.error_text = ""
        self._set_message("")
        full = self._full_cache.get(str(pic.primary)) if self.zoomed else None
        self._put(full if full is not None else qimg, keep_centre=False)
        self._paint_pending = index
        self.shown.emit(index)
        if self.zoomed and full is None:
            self._request_full()
        self._sync_hud()

    def _put(self, qimg: QImage, keep_centre: bool) -> None:
        frac = None
        if keep_centre and self.has_image:
            c = self.mapToScene(self.viewport().rect().center())
            old = self._item.pixmap()
            frac = (c.x() / max(1, old.width()), c.y() / max(1, old.height()))
        self._item.setPixmap(QPixmap.fromImage(qimg))
        self._scene.setSceneRect(self._item.boundingRect())
        self.pixmap_changed.emit(self.index)
        self._apply_zoom()
        if frac is not None and self.zoomed:
            self.centerOn(QPointF(frac[0] * qimg.width(), frac[1] * qimg.height()))

    def _apply_zoom(self) -> None:
        if not self.has_image:
            return
        if self.zoomed:
            self.resetTransform()
            dpr = self.devicePixelRatioF()
            if dpr > 1.0:  # 100% means one image pixel per device pixel, not per logical pixel
                self.scale(1.0 / dpr, 1.0 / dpr)
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            frac = None
            if self.zoom_target is not None:
                try:
                    frac = self.zoom_target()
                except Exception:  # noqa: BLE001 - a bad target must never break Z
                    frac = None
            if frac is None:
                self.centerOn(self._item)
            else:
                pm = self._item.pixmap()
                self.centerOn(QPointF(frac[0] * pm.width(), frac[1] * pm.height()))
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    # -- zoom and full decode ---------------------------------------------------------------------
    def toggle_zoom(self) -> None:
        self.zoomed = not self.zoomed
        if self.zoomed:
            self._apply_zoom()
            if not self._swap_cached_full():
                self._request_full()
        else:
            if self._preview is not None:
                self._put(self._preview, keep_centre=False)  # the 1:1 pixmap is not needed to fit
        self._sync_hud()
        self.zoom_changed.emit(self.zoomed)

    def _swap_cached_full(self) -> bool:
        if self.pic is None:
            return False
        full = self._full_cache.get(str(self.pic.primary))
        if full is None:
            return False
        self._put(full, keep_centre=True)
        return True

    def _request_full(self) -> None:
        if self.pic is None or self.index < 0 or self._preview is None:
            return
        if self._full.get(self.index) == "pending":
            return
        self._full[self.index] = "pending"
        self.pool.start(_FullJob(self, self.index, self.pic.primary, self.info))

    def _on_full(self, index: int, path: str, qimg, ms: float, status: str) -> None:
        if status == "stale":
            self._full.pop(index, None)
            if index == self.index and self.zoomed:  # the user came back before this was processed
                self._request_full()
            self._sync_hud()
            return
        self._full[index] = "done" if status == "done" else ("skipped" if status == "skipped" else "error")
        if status == "done" and qimg is not None:
            self._full_cache[path] = qimg
            self._full_cache.move_to_end(path)
            while len(self._full_cache) > FULL_CACHE:
                self._full_cache.popitem(last=False)
            if index == self.index and self.pic is not None and str(self.pic.primary) == path and self.zoomed:
                self._put(qimg, keep_centre=True)
            self.full_done.emit(index, ms)
        elif status == "skipped":
            self.full_skipped.emit(index)
        else:
            self.full_failed.emit(index, status)
        self._sync_hud()

    def _sync_hud(self) -> None:
        self.hud.setVisible(self._full.get(self.index) == "pending" and self.zoomed)
        self._place()

    # -- right-click: reveal the picture in the file manager ----------------------------------------
    def context_menu_for(self, pos):
        """The menu for a right-click at `pos`, or None when no picture is on screen."""
        return None if self.pic is None else reveal_menu(self, self.pic)

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        menu = self.context_menu_for(e.pos())
        if menu is not None:
            menu.exec(e.globalPos())
            e.accept()

    # -- overlays and painting --------------------------------------------------------------------
    def _place(self) -> None:
        vp = self.viewport().rect()
        self.message.setGeometry(vp.adjusted(40, 40, -40, -40))
        self.hud.move(vp.right() - self.hud.width() - 12, 12)
        h = self.strip.sizeHint().height()
        self.strip.setGeometry(0, vp.height() - h, vp.width(), h)
        self.hud.raise_()
        self.strip.raise_()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if not self.zoomed and self.has_image:
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        self._place()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self._paint_pending is not None:
            i, self._paint_pending = self._paint_pending, None
            self.painted.emit(i)

    def retheme(self) -> None:
        c = theme.current()
        self.setBackgroundBrush(QColor(c.stage))
        self.message.setStyleSheet(f"color: {c.dim}; font-size: 15px;")
        self.viewport().update()

    def shutdown(self, timeout_ms: int = 3000) -> None:
        self.pool.clear()
        self.pool.waitForDone(timeout_ms)

