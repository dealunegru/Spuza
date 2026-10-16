"""Compare: up to four near-twins side by side, zoom and pan locked together (flag compare_view).

Contract for the window (wired by spz-nx-wire). The window adds one `CompareView` as a page of its stack and
calls `open(indices, focus)`; the view owns its keys while shown:
  ← → K J  move focus (K back, J next, as in the window) between cells (never off the set)    Space M  mark/unmark the focused cell
  Z        the focused cell and its neighbour (two at most: memory) to 100% of each embedded JPEG at the same fractional point; drag pans all
  N        2 -> 3 -> 4 cells when opened on a run from the single view; on 4 (or a grid pick) it closes
  Esc      closes
Fitted cells show the prefetcher's cached QImages (no decode); a cell outside the cache, and every zoomed cell,
decodes on the view's own worker pool. Owned by task spz-nx-compare.

`open(indices, focus)`: `focus` is a position in `indices` (clamped), not a catalog index. Everything is inert
unless the flag is on: `open` does nothing in classic mode.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QPointF, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from spuza import features
from spuza.ui import theme

MAX_CELLS = 4
ZOOM_CELLS = 2  # cells at 100% at once: four full GR III decodes held +577 MB working set (measured)
FIT_SIDE = 2560  # decode size of a fitted cell the prefetcher does not hold
POOL_THREADS = 2
CAPTION_ZOOMED = "preview 100%"
CAPTION_LOADING = "decoding…"
_NO_REPEAT = (Qt.Key.Key_Space, Qt.Key.Key_M, Qt.Key.Key_Z, Qt.Key.Key_N)


class _Signals(QObject):
    done = Signal(int, int, int, object, str)  # generation, catalog index, max_side (0 = full), QImage|None, error


class _Job(QRunnable):
    def __init__(self, sig: _Signals, loader: Callable, gen: int, index: int, path, max_side: int | None):
        super().__init__()
        self.sig, self.loader, self.gen, self.index, self.path, self.max_side = sig, loader, gen, index, path, max_side
        self.setAutoDelete(True)

    def run(self) -> None:
        side = self.max_side or 0
        try:
            img, _info = self.loader(self.path, self.max_side)
            rgb = img.convert("RGB")
            w, h = rgb.size
            q = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        except Exception as exc:  # noqa: BLE001 - a bad picture shows a message, never raises on a worker
            self._emit(side, None, str(exc) or type(exc).__name__)
            return
        self._emit(side, q, "")

    def _emit(self, side: int, q, err: str) -> None:
        try:
            self.sig.done.emit(self.gen, self.index, side, q, err)
        except RuntimeError:  # the view was destroyed while we decoded
            pass


class _Cell(QGraphicsView):
    """One picture: raster QGraphicsView, fitted or at 100% of its embedded preview."""

    clicked = Signal(int)
    panned = Signal(object)  # this cell, after the user moved it

    def __init__(self, index: int, name: str, parent=None):
        super().__init__(parent)
        self.index, self.name = index, name
        self.fit_img: QImage | None = None
        self.zoom_img: QImage | None = None
        self.zoomed = False
        self.native_w = 0  # width of the embedded preview, for upscaling the fitted image meanwhile
        self.centre = (0.5, 0.5)
        self._quiet = False
        self._mask: QImage | None = None
        self._scene = QGraphicsScene(self)
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self.item)
        self.overlay = QGraphicsPixmapItem(self.item)
        self.overlay.setVisible(False)
        self.setScene(self._scene)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        vp = self.viewport()
        self.tag = QLabel(name, vp)
        self.tag.setStyleSheet("background: rgba(0,0,0,150); color: #fff; padding: 2px 8px; border-radius: 6px;")
        self.tag.move(8, 8)
        self.tag.adjustSize()
        self.message = QLabel(vp)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #ccc;")
        self.caption = QLabel(vp)
        self.caption.setStyleSheet("background: rgba(0,0,0,150); color: #fff; padding: 1px 8px; border-radius: 6px;")
        self.caption.hide()
        self.set_message("Loading…")
        self.horizontalScrollBar().valueChanged.connect(self._moved)
        self.verticalScrollBar().valueChanged.connect(self._moved)

    # -- content ----------------------------------------------------------------------------------
    @property
    def has_image(self) -> bool:
        return not self.item.pixmap().isNull()

    def set_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self._place()

    def set_marked(self, on: bool) -> None:
        self.tag.setText(f"{self.name}  ✓ marked" if on else self.name)
        self.tag.adjustSize()

    def set_fit(self, img: QImage) -> None:
        self.fit_img = img
        if self.zoom_img is None:
            self._put(img)
        self.set_message("")

    def set_zoom_image(self, img: QImage) -> None:
        self.zoom_img = img
        if self.zoomed:
            self._put(img)
            self.caption.setText(CAPTION_ZOOMED)
            self.caption.adjustSize()
            self._place()

    def _put(self, img: QImage) -> None:
        self._quiet = True
        try:
            self.item.setPixmap(QPixmap.fromImage(img))
            self._scene.setSceneRect(self.item.boundingRect())
            self._apply()
        finally:
            self._quiet = False
        self._apply_mask()

    def _scale(self) -> float:
        dpr = self.devicePixelRatioF()
        if self.zoom_img is not None or self.fit_img is None:
            return 1.0 / dpr
        w = self.fit_img.width()
        return (self.native_w / w if self.native_w > w else 1.0) / dpr  # the fitted image upscaled until the decode

    def _apply(self) -> None:
        if not self.has_image:
            return
        self._quiet = True
        try:
            self.resetTransform()
            if self.zoomed:
                s = self._scale()
                self.scale(s, s)
                self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
                self.set_centre(self.centre)
            else:
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                self.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatio)
        finally:
            self._quiet = False

    def enter_zoom(self, centre: tuple[float, float], has_full: bool) -> None:
        self.zoomed, self.centre = True, centre
        self.caption.setText(CAPTION_ZOOMED if has_full else CAPTION_LOADING)
        self.caption.adjustSize()
        self.caption.show()
        self.overlay.setVisible(False)
        self._place()
        self._apply()

    def leave_zoom(self) -> None:
        self.zoomed, self.zoom_img = False, None  # frees the full-size image
        self.caption.hide()
        if self.fit_img is not None:
            self._put(self.fit_img)
        else:
            self._apply()

    def set_mask(self, mask: QImage | None) -> None:
        self._mask = mask
        self._apply_mask()

    def _apply_mask(self) -> None:
        m, pm = self._mask, self.item.pixmap()
        if m is None or m.isNull() or pm.isNull() or self.zoomed:
            self.overlay.setVisible(False)
            return
        self.overlay.setPixmap(QPixmap.fromImage(m))
        self.overlay.setTransform(QTransform.fromScale(pm.width() / m.width(), pm.height() / m.height()))
        self.overlay.setVisible(True)

    # -- pan --------------------------------------------------------------------------------------
    def centre_fraction(self) -> tuple[float, float]:
        pm = self.item.pixmap()
        c = self.mapToScene(self.viewport().rect().center())
        return (c.x() / max(1, pm.width()), c.y() / max(1, pm.height()))

    def set_centre(self, frac: tuple[float, float]) -> None:
        self.centre = frac
        pm = self.item.pixmap()
        if pm.isNull():
            return
        was, self._quiet = self._quiet, True
        try:
            self.centerOn(QPointF(frac[0] * pm.width(), frac[1] * pm.height()))
        finally:
            self._quiet = was

    def _moved(self, _v=0) -> None:
        if self.zoomed and not self._quiet and self.has_image:
            self.centre = self.centre_fraction()
            self.panned.emit(self)

    # -- events -----------------------------------------------------------------------------------
    def _place(self) -> None:
        vp = self.viewport().rect()
        self.message.setGeometry(vp.adjusted(16, 16, -16, -16))
        self.caption.move(vp.right() - self.caption.width() - 8, vp.bottom() - self.caption.height() - 8)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._apply()
        self._place()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.clicked.emit(self.index)
        super().mousePressEvent(e)

    def wheelEvent(self, e) -> None:  # noqa: N802
        e.ignore()

    def shutdown_scene(self) -> None:
        self.item.setPixmap(QPixmap())
        self.overlay.setPixmap(QPixmap())
        self.fit_img = self.zoom_img = self._mask = None


class CompareView(QWidget):
    closed = Signal(int)  # the catalog index focused when compare closed
    marks_changed = Signal()  # a mark was toggled from compare
    focus_changed = Signal(int)  # the focused cell now shows catalog index

    def __init__(self, catalog, prefetcher, parent=None, loader=None):
        """`loader(path, max_side)` -> (PIL image, PreviewInfo), default `spuza.preview.load`; tests inject one."""
        super().__init__(parent)
        self.catalog, self.prefetcher = catalog, prefetcher
        self._loader = loader
        self._cells: list[_Cell] = []
        self._focus = 0  # position in _cells
        self._run = False
        self._zoomed = False
        self._advance = False
        self._gen = 0
        self._syncing = False
        self._pending: set[tuple[int, int]] = set()  # (index, max_side) decodes in flight
        self._masks: dict[int, QImage] = {}
        self._sig = _Signals()
        self._sig.done.connect(self._on_decoded)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(POOL_THREADS)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(6, 6, 6, 6)
        self._row.setSpacing(6)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if prefetcher is not None and hasattr(prefetcher, "ready"):
            prefetcher.ready.connect(self._on_prefetched)

    # -- public -----------------------------------------------------------------------------------
    def open(self, indices: list[int], focus: int = 0, run: bool = True) -> None:
        """Show these catalog indices (1-4; more are cut to 4). `run`: opened on a run from the single view, so N
        grows the set with the following pictures; False for a review-grid pick, where N closes."""
        if not features.enabled("compare_view"):
            return
        n = len(self.catalog.pictures)
        keep: list[int] = []
        for i in indices:
            if isinstance(i, int) and 0 <= i < n and i not in keep:
                keep.append(i)
        keep = keep[:MAX_CELLS]
        if not keep:
            return
        self._reset()
        self._run = bool(run)
        for i in keep:
            self._add_cell(i)
        self._focus = max(0, min(int(focus), len(keep) - 1))
        self._refresh_chrome()
        self.setFocus()
        self.focus_changed.emit(self.focused_index())

    def indices(self) -> list[int]:
        return [c.index for c in self._cells]

    def focused_index(self) -> int:
        return self._cells[self._focus].index if self._cells else -1

    def is_zoomed(self) -> bool:
        return self._zoomed

    def set_advance_on_mark(self, on: bool) -> None:
        """Move on mark: marking moves focus to the next cell, never outside the set."""
        self._advance = bool(on)

    def set_overlay(self, index: int, img: QImage | None) -> None:
        """Draw `img` (a peaking mask, any size) scaled over the fitted cell showing `index`; None removes it."""
        if img is None or img.isNull():
            self._masks.pop(index, None)
        else:
            self._masks[index] = img
        for c in self._cells:
            if c.index == index:
                c.set_mask(self._masks.get(index))

    def status_text(self) -> str:
        marked = sum(1 for c in self._cells if self.catalog.is_marked(c.index))
        return f"compare {len(self._cells)} · {marked} marked"

    def cell(self, index: int) -> _Cell | None:
        """The cell showing catalog `index` (tests, overlays)."""
        return next((c for c in self._cells if c.index == index), None)

    def close_compare(self) -> None:
        """Leave compare: frees zoomed decodes and emits closed(focused_index())."""
        if not self._cells:
            return
        idx = self.focused_index()
        self._reset()
        self.closed.emit(idx)

    def shutdown(self, timeout_ms: int = 3000) -> None:
        self._gen += 1
        self._pool.clear()
        self._pool.waitForDone(timeout_ms)

    def wait_idle(self, timeout_ms: int = 5000) -> bool:
        """Block until running decodes finish (tests); bounded by timeout_ms."""
        return self._pool.waitForDone(timeout_ms)

    # -- cells ------------------------------------------------------------------------------------
    def _get_loader(self) -> Callable:
        if self._loader is None:
            from spuza.preview import load  # lazy: keeps decoders out of module import

            self._loader = load
        return self._loader

    def _reset(self) -> None:
        self._gen += 1  # in-flight decodes now belong to nobody
        self._pool.clear()
        self._pending.clear()
        self._zoomed = False
        for c in self._cells:
            self._row.removeWidget(c)
            c.shutdown_scene()
            c.hide()
            c.deleteLater()
        self._cells = []
        self._masks.clear()
        self._focus = 0

    def _add_cell(self, i: int) -> None:
        pic = self.catalog.pictures[i]
        cell = _Cell(i, pic.stem, self)
        cell.clicked.connect(self._on_clicked)
        cell.panned.connect(self._on_panned)
        self._row.addWidget(cell, 1)
        self._cells.append(cell)
        info = self.prefetcher.info(i) if self.prefetcher is not None and hasattr(self.prefetcher, "info") else None
        cell.native_w = int(getattr(info, "width", 0) or 0)
        cell.set_marked(self.catalog.is_marked(i))
        img = self.prefetcher.cached(i) if self.prefetcher is not None else None
        if img is not None and not img.isNull():
            cell.set_fit(img)
        else:
            self._request(i, FIT_SIDE)
        if i in self._masks:
            cell.set_mask(self._masks[i])
        cell.show()

    def _request(self, i: int, max_side: int | None) -> None:
        key = (i, max_side or 0)
        if key in self._pending:
            return
        self._pending.add(key)
        self._pool.start(_Job(self._sig, self._get_loader(), self._gen, i, self.catalog.pictures[i].primary, max_side))

    def _on_decoded(self, gen: int, index: int, side: int, img, err: str) -> None:
        if gen != self._gen:
            return  # from before the last open/close/unzoom
        self._pending.discard((index, side))
        cell = self.cell(index)
        if cell is None:
            return
        if side == 0:  # full size
            if not cell.zoomed:
                return
            if img is not None and not img.isNull():
                cell.set_zoom_image(img)
            else:
                cell.caption.setText("preview failed")
                cell.caption.adjustSize()
            return
        if cell.fit_img is not None:
            return
        if img is None or img.isNull():
            cell.set_message(f"Cannot show {self.catalog.pictures[index].primary.name}\n{err}")
        else:
            cell.set_fit(img)

    def _on_prefetched(self, i: int, img=None, _info=None) -> None:
        cell = self.cell(i)
        if cell is not None and cell.fit_img is None:
            got = self.prefetcher.cached(i)
            if got is not None and not got.isNull():
                cell.set_fit(got)

    def _on_clicked(self, index: int) -> None:
        for pos, c in enumerate(self._cells):
            if c.index == index:
                self._set_focus(pos)

    def _refresh_chrome(self) -> None:
        c = theme.current()
        for pos, cell in enumerate(self._cells):
            colour = c.accent if pos == self._focus else "transparent"
            cell.setStyleSheet(f"QGraphicsView {{ border: 3px solid {colour}; }}")
            cell.setBackgroundBrush(QColor(c.stage))
            cell.set_marked(self.catalog.is_marked(cell.index))

    def border_colour(self, index: int) -> str:
        """The colour of `index`'s cell border (accent when focused, else transparent); "" for no such cell."""
        cell = self.cell(index)
        css = cell.styleSheet() if cell is not None else ""
        return css.split("solid ", 1)[1].split(";", 1)[0] if "solid " in css else ""

    def _set_focus(self, pos: int) -> None:
        pos = max(0, min(pos, len(self._cells) - 1))
        if pos == self._focus:
            return
        self._focus = pos
        self._refresh_chrome()
        self.focus_changed.emit(self.focused_index())

    # -- zoom -------------------------------------------------------------------------------------
    def toggle_zoom(self) -> None:
        if not self._cells:
            return
        if self._zoomed:
            self._zoomed = False
            self._gen += 1  # full-size results still in flight are dropped
            self._pool.clear()
            self._pending.clear()
            for c in self._cells:
                if c.zoomed:
                    c.leave_zoom()
            for c in self._cells:  # a fitted decode dropped with the generation is asked for again
                if c.fit_img is None:
                    self._request(c.index, FIT_SIDE)
            return
        self._zoomed = True
        for c in self._zoom_set():
            c.enter_zoom((0.5, 0.5), False)
            self._request(c.index, None)

    def _zoom_set(self) -> list[_Cell]:
        """The cells Z opens at 100%: the focused one and its neighbours, at most ZOOM_CELLS of them."""
        order = sorted(range(len(self._cells)), key=lambda p: (abs(p - self._focus), p))
        return [self._cells[p] for p in sorted(order[:ZOOM_CELLS])]

    def _on_panned(self, src: _Cell) -> None:
        if self._syncing or not self._zoomed:
            return
        frac = src.centre_fraction()
        self._syncing = True
        try:
            for c in self._cells:
                if c is not src and c.zoomed:
                    c.set_centre(frac)
        finally:
            self._syncing = False

    # -- keys -------------------------------------------------------------------------------------
    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        k = ev.key()
        if not self._cells:
            super().keyPressEvent(ev)
            return
        if ev.isAutoRepeat() and k in _NO_REPEAT:
            return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_K):
            self._set_focus(self._focus - 1)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_J):
            self._set_focus(self._focus + 1)
        elif k in (Qt.Key.Key_Space, Qt.Key.Key_M):
            self._mark_focused()
        elif k == Qt.Key.Key_Z:
            self.toggle_zoom()
        elif k == Qt.Key.Key_N:
            self._grow()
        elif k == Qt.Key.Key_Escape:
            self.close_compare()
        else:
            super().keyPressEvent(ev)

    def _mark_focused(self) -> None:
        self.catalog.toggle_mark(self.focused_index())
        self._refresh_chrome()
        self.marks_changed.emit()
        if self._advance and self._focus + 1 < len(self._cells):
            self._set_focus(self._focus + 1)

    def _grow(self) -> None:
        nxt = max(self.indices()) + 1
        if not self._run or len(self._cells) >= MAX_CELLS or nxt >= len(self.catalog.pictures):
            self.close_compare()
            return
        self._add_cell(nxt)
        self._refresh_chrome()
