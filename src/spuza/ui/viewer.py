"""The big picture: a QGraphicsView on the raster backend (never OpenGL).

Fit-to-window by default; Z toggles 1:1 at the centre and the state carries to the next picture. Z shows the
native pixels of the picture: the fitted view uses the fast capped decode, so on Z a worker decodes the real
size (a raw whose embedded preview is much smaller than the frame of the camera, dec-044, is demosaiced and
cropped to that frame; any other picture is decoded again at the native size of its preview, uncapped). The
result replaces the preview only if that picture is still on screen. The picture is shown turned by
rotation_of(index) quarter turns, applied to the item at display time; the caches hold the unrotated pixels.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from PySide6.QtCore import QEvent, QPointF, QTimer, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
                               QLabel, QWidget)

from spuza.reveal import reveal_menu
from spuza.ui import theme

REVEAL_MS = 1000  # full-bleed: how long the strip stays after the last pointer move
FULL_CACHE = 3 # full-resolution decodes kept (a 24 MP image is ~70 MB as a pixmap)


def _default_needs_full_decode(path, info) -> bool:
    from spuza.preview import needs_full_decode  # lazy: keeps rawpy out of import time

    return needs_full_decode(path, info)


def _default_full_decode(path, half_size: bool = False):
    from spuza.preview import full_decode

    return full_decode(path, half_size)


def _default_sensor_geometry(path):
    from spuza.preview import sensor_geometry

    return sensor_geometry(path)


def _default_sensor_decode(path):
    from spuza.preview import full_decode

    return full_decode(path, False, crop=False)  # the whole visible sensor, not the camera's crop frame


def _default_native_decode(path):
    from spuza.preview import native_preview

    return native_preview(path)


def _default_sidecar_decode(sidecar):
    from spuza.preview import sidecar_native

    return sidecar_native(sidecar)[0]  # the JPEG at its own size, oriented; raises PreviewError if unreadable


def _jpeg_sidecar(pic):
    """The picture's .jpg/.jpeg sidecar (a .hif one does not count), else None."""
    from spuza.preview import is_jpeg_sidecar

    side = getattr(pic, "sidecar", None)
    return side if is_jpeg_sidecar(side) else None


def _is_capped(shown, info) -> bool:
    from spuza.preview import is_capped

    return is_capped(shown, info)


def to_qimage(im) -> QImage:
    rgb = im.convert("RGB")
    w, h = rgb.size
    return QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class _FullJob(QRunnable):
    def __init__(self, viewer: Viewer, index: int, path, info, shown: tuple[int, int] = (0, 0),
                 want_sensor: bool = False, sidecar=None):
        super().__init__()
        self.sidecar = sidecar  # the picture's JPEG sidecar, if any: its pixels are the picture at 100%
        self.viewer, self.index, self.path, self.info = viewer, index, path, info
        self.want_sensor = want_sensor  # the "full sensor" preference when Z was pressed
        self.shown = shown  # size of the (capped) preview on screen when Z was pressed
        self.setAutoDelete(True)

    def _emit(self, *args) -> None:
        try:
            self.viewer._full_result.emit(*args)
        except RuntimeError:  # the viewer was destroyed while we decoded
            pass

    def run(self) -> None:
        v, t0 = self.viewer, time.perf_counter()
        extra = None  # what the preference found out about this file (None: it was not asked)
        try:
            if v.index != self.index:  # the user moved on while this waited in the queue
                self._emit(self.index, str(self.path), None, 0.0, "stale", None)
                return
            if self.info is None:
                self._emit(self.index, str(self.path), None, 0.0, "skipped", None)
                return
            geo = v.sensor_geometry(self.path) if self.want_sensor else None
            if self.want_sensor:
                extra = {"geo": geo, "sensor": geo is not None}
            if geo is not None:  # cropped in camera + the preference: the whole sensor at native pixels
                qimg = to_qimage(v.sensor_decode(self.path))
            else:
                qimg = None
                if self.sidecar is not None:
                    try:
                        qimg = to_qimage(v.sidecar_decode(self.sidecar))
                    except Exception:  # noqa: BLE001 - an unreadable JPEG falls back to the raw rule
                        extra = {**(extra or {}), "jpg_bad": True}
            if qimg is not None:  # the JPEG sidecar: the picture's own pixels, no rawpy
                pass
            elif v.needs_full_decode(self.path, self.info):
                qimg = to_qimage(v.full_decode(self.path, False))
            elif _is_capped(self.shown, self.info):
                qimg = to_qimage(v.native_decode(self.path))  # the preview is full size; the screen copy is not
            else:
                self._emit(self.index, str(self.path), None, 0.0, "skipped", extra)  # the preview on screen is native
                return
        except Exception as exc:  # noqa: BLE001 - reported to the HUD, never raised on a worker
            self._emit(self.index, str(self.path), None, 0.0, str(exc) or type(exc).__name__, extra)
            return
        self._emit(self.index, str(self.path), qimg, (time.perf_counter() - t0) * 1000, "done", extra)


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
    _full_result = Signal(int, str, object, float, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rotation_of = lambda i: 0  # contract fr3: clockwise quarter turns per index; the window assigns it
        self.needs_full_decode = _default_needs_full_decode
        self.full_decode = _default_full_decode
        self.native_decode = _default_native_decode
        self.sensor_geometry = _default_sensor_geometry  # (crop box, sensor size) of an in-camera-cropped raw, else None
        self.sensor_decode = _default_sensor_decode
        self.sidecar_decode = _default_sidecar_decode  # JPEG sidecar -> its own pixels (no rawpy)
        self._jpg_bad: set[str] = set()  # primaries whose JPEG sidecar would not open (Z fell back to the raw)
        self.zoom_full_sensor = False  # the "Zoom shows the full sensor on cropped shots" preference
        self._geo: dict[str, tuple | None] = {}  # path -> geometry (cropped) or None (not), once a worker has looked
        self._shown_sensor = False  # the pixmap on screen is the whole sensor, not the camera's frame
        self.index = -1
        self.pic = None
        self.info = None
        self.zoomed = False
        self.error_text = ""
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._preview: QImage | None = None
        self._full_cache: OrderedDict[tuple[str, str], QImage] = OrderedDict()  # (path, "frame" | "sensor")
        self._full: dict[int, str] = {}  # index -> pending | done | skipped | error
        self._shown_native = False  # the pixmap on screen is the native pixels (a full decode)
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
        # Full-bleed: the strip shows while the pointer moves over the picture and hides REVEAL_MS after the last move.
        self.bleed = False
        self.reveal_ms = REVEAL_MS
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._hide_strip)
        self.setMouseTracking(True)  # plain moves (no button) must reach us
        vp.setMouseTracking(True)
        vp.installEventFilter(self)  # QGraphicsView hands moves to the viewport first; panning is untouched
        self._full_result.connect(self._on_full)
        self.retheme()

    # -- full-bleed strip: hidden at rest, revealed by pointer movement ------------------------------
    def set_bleed(self, on: bool) -> None:
        """Enter or leave full-bleed. Either way the strip is hidden and the reveal timer stopped."""
        self.bleed = bool(on)
        self._reveal_timer.stop()
        self.strip.setVisible(False)

    def reveal_strip(self) -> None:
        """Show the strip and (re)start the hide timer; does nothing outside full-bleed."""
        if not self.bleed:
            return
        self.strip.setVisible(True)
        self._place()
        self._reveal_timer.start(self.reveal_ms)

    def _hide_strip(self) -> None:
        self.strip.setVisible(False)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.viewport() and ev.type() == QEvent.Type.MouseMove:
            self.reveal_strip()
        return False  # never swallow: ScrollHandDrag panning still gets the event

    # -- state ------------------------------------------------------------------------------------
    @property
    def has_image(self) -> bool:
        return not self._item.pixmap().isNull()

    @property
    def hud_visible(self) -> bool:
        return not self.hud.isHidden()

    def image_size(self) -> tuple[int, int] | None:
        """The picture as shown: width and height swap under an odd number of quarter turns."""
        pm = self._item.pixmap()
        if pm.isNull():
            return None
        return (pm.height(), pm.width()) if self._quarters() % 2 else (pm.width(), pm.height())

    def _quarters(self) -> int:
        try:
            return int(self.rotation_of(self.index)) % 4
        except Exception:  # noqa: BLE001 - a bad rotation provider shows the picture unrotated
            return 0

    def zoom_text(self) -> str:
        """What the HUD may say about the zoom: "100%" only while native pixels are on screen."""
        if not self.zoomed:
            return ""
        st = self._full.get(self.index)
        if st == "pending":
            return "100% … decoding full resolution…"
        if self._shown_sensor:
            return "100% · full sensor"
        if self._shown_native and self.pic is not None and str(self.pic.primary) in self._jpg_bad:
            return "100% · JPG unreadable, from the raw"
        if self._shown_native or st == "skipped":
            return "100%"
        if st == "error":
            return "preview pixels only (full decode failed)"
        return "100% …"

    @property
    def sensor_shown(self) -> bool:
        """True while the whole sensor (not the camera's frame) is on screen."""
        return self._shown_sensor

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
        self._shown_native = False
        self._shown_sensor = False
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
        hit = self._cached_full(str(pic.primary)) if self.zoomed else None
        full, sensor = hit if hit is not None else (None, False)
        self._put(full if full is not None else qimg, keep_centre=False, native=full is not None, sensor=sensor)
        self._paint_pending = index
        self.shown.emit(index)
        if self.zoomed and full is None:
            self._request_full()
        self._sync_hud()

    def _centre_frac(self):
        """The viewport centre as a fraction of the picture, in the own (unrotated) frame of the picture."""
        old = self._item.pixmap()
        c = self._item.mapFromScene(self.mapToScene(self.viewport().rect().center()))
        return (c.x() / max(1, old.width()), c.y() / max(1, old.height()))

    def _orient_item(self) -> None:
        """Turn the item by rotation_of(index) quarter turns about its centre; the scene follows its bounds."""
        pm = self._item.pixmap()
        self._item.setTransformOriginPoint(pm.width() / 2, pm.height() / 2)
        self._item.setRotation(90.0 * self._quarters())
        self._scene.setSceneRect(self._item.sceneBoundingRect())

    @staticmethod
    def _map_frac(frac, geo, to_sensor: bool):
        """A fraction of the camera's frame <-> the same subject as a fraction of the whole sensor."""
        (left, top, cw, ch), (w, h) = geo
        if to_sensor:
            return ((left + frac[0] * cw) / max(1, w), (top + frac[1] * ch) / max(1, h))
        return (min(1.0, max(0.0, (frac[0] * w - left) / max(1, cw))),
                min(1.0, max(0.0, (frac[1] * h - top) / max(1, ch))))

    def _put(self, qimg: QImage, keep_centre: bool, native: bool = False, sensor: bool = False) -> None:
        frac = self._centre_frac() if keep_centre and self.has_image else None
        geo = self._geo.get(str(self.pic.primary)) if self.pic is not None else None
        if geo is not None and self.zoomed:
            if frac is not None and sensor != self._shown_sensor:  # the other picture: same subject, other fractions
                frac = self._map_frac(frac, geo, sensor)
            elif frac is None and sensor:  # the crop frame centre (or the target point) in the sensor
                try:
                    target = self.zoom_target() if self.zoom_target is not None else None
                except Exception:  # noqa: BLE001 - a bad target must never break Z
                    target = None
                frac = self._map_frac(target or (0.5, 0.5), geo, True)
        self._shown_native = native
        self._shown_sensor = sensor
        self._item.setPixmap(QPixmap.fromImage(qimg))
        self._orient_item()
        self.pixmap_changed.emit(self.index)
        self._apply_zoom(frac if self.zoomed else None)

    def rotation_changed(self, i: int) -> None:
        """Picture i's rotation changed: if it is on screen, turn it now, keeping zoom and the centred point."""
        if i != self.index or not self.has_image:
            return
        frac = self._centre_frac() if self.zoomed else None
        self._orient_item()
        self._apply_zoom(frac)
        self.viewport().update()

    def _apply_zoom(self, frac=None) -> None:
        if not self.has_image:
            return
        if self.zoomed:
            self.resetTransform()
            dpr = self.devicePixelRatioF()
            if dpr > 1.0:  # 100% means one image pixel per device pixel, not per logical pixel
                self.scale(1.0 / dpr, 1.0 / dpr)
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            if frac is None and self.zoom_target is not None:
                try:
                    frac = self.zoom_target()
                except Exception:  # noqa: BLE001 - a bad target must never break Z
                    frac = None
            if frac is None:
                self.centerOn(self._item)
            else:  # a fraction of the own frame of the picture; the turn of the item maps it into the scene
                pm = self._item.pixmap()
                self.centerOn(self._item.mapToScene(QPointF(frac[0] * pm.width(), frac[1] * pm.height())))
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

    def _wanted_variant(self, path: str) -> str:
        """"frame" (the camera's frame), "sensor" (the whole sensor) or "?" (the preference is on and no worker
        has yet said whether this file is cropped)."""
        if not self.zoom_full_sensor:
            return "frame"
        if path not in self._geo:
            return "?"
        return "frame" if self._geo[path] is None else "sensor"

    def _cached_full(self, path: str):
        """(image, is_sensor) of the cached full decode that matches the preference, else None."""
        want = self._wanted_variant(path)
        full = None if want == "?" else self._full_cache.get((path, want))
        return None if full is None else (full, want == "sensor")

    def set_zoom_full_sensor(self, on: bool) -> None:
        """The preference changed: if Z is on, show the variant it now asks for."""
        on = bool(on)
        if on == self.zoom_full_sensor:
            return
        self.zoom_full_sensor = on
        if self.zoomed and self.pic is not None and self.has_image:
            if not self._swap_cached_full():
                self._request_full()
            self._sync_hud()

    def _swap_cached_full(self) -> bool:
        if self.pic is None:
            return False
        hit = self._cached_full(str(self.pic.primary))
        if hit is None:
            return False
        self._put(hit[0], keep_centre=True, native=True, sensor=hit[1])
        return True

    def _request_full(self) -> None:
        if self.pic is None or self.index < 0 or self._preview is None:
            return
        if self._full.get(self.index) == "pending":
            return
        self._full[self.index] = "pending"
        shown = (self._preview.width(), self._preview.height())
        self.pool.start(_FullJob(self, self.index, self.pic.primary, self.info, shown, self.zoom_full_sensor,
                                  _jpeg_sidecar(self.pic)))

    def _on_full(self, index: int, path: str, qimg, ms: float, status: str, extra=None) -> None:
        if status == "stale":
            self._full.pop(index, None)
            if index == self.index and self.zoomed:  # the user came back before this was processed
                self._request_full()
            self._sync_hud()
            return
        self._full[index] = "done" if status == "done" else ("skipped" if status == "skipped" else "error")
        if isinstance(extra, dict) and "geo" in extra:
            self._geo[path] = extra["geo"]
        if isinstance(extra, dict) and extra.get("jpg_bad"):
            self._jpg_bad.add(path)
        got_sensor = bool(isinstance(extra, dict) and extra.get("sensor"))
        on_screen = index == self.index and self.pic is not None and str(self.pic.primary) == path and self.zoomed
        if status == "done" and qimg is not None:
            key = (path, "sensor" if got_sensor else "frame")
            self._full_cache[key] = qimg
            self._full_cache.move_to_end(key)
            while len(self._full_cache) > FULL_CACHE:
                self._full_cache.popitem(last=False)
            if on_screen and self._wanted_variant(path) == key[1]:
                self._put(qimg, keep_centre=True, native=True, sensor=got_sensor)
            elif on_screen:  # the preference changed while this decoded: fetch the variant it asks for now
                self._full.pop(index, None)
                if not self._swap_cached_full():
                    self._request_full()
            self.full_done.emit(index, ms)
        elif status == "skipped":
            if on_screen and not extra and self.info is not None and self._wanted_variant(path) != "frame":
                self._full.pop(index, None)  # asked before the preference was on: look again with it
                self._request_full()
            elif on_screen and self._shown_sensor and self._preview is not None:  # the preview is the native frame
                self._put(self._preview, keep_centre=True, native=False, sensor=False)
            self.full_skipped.emit(index)
        else:
            self.full_failed.emit(index, status)
        self._sync_hud()

    def _sync_hud(self) -> None:
        self.hud.setText(self.zoom_text())
        self.hud.adjustSize()
        # a failure stays up while zoomed; so does "full sensor", so it is never mistaken for the camera's frame
        bad_jpg = self.pic is not None and str(self.pic.primary) in self._jpg_bad and self._shown_native
        self.hud.setVisible(self.zoomed and (self._full.get(self.index) in ("pending", "error") or self._shown_sensor
                                             or bad_jpg))
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

