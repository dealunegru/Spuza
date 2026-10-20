"""Review grid: the marked pictures as thumbnails; unmark, zoom, open one in the cull view.

Keys: M or Space unmarks the selected picture (Delete is deliberately NOT bound: nothing is ever
deleted), Z or double-click zooms (Z again toggles fit / 100%), Return/Enter emits ``open_index``,
Esc leaves zoom, or on the grid emits ``closed``.

Z shows the native pixels: the zoom page opens on the fast ZOOM_SIDE decode (fitted), and going to 100% loads
the real size on a worker (a plain image or a good embedded preview uncapped, a raw with a small preview
demosaiced and cropped to its frame) and swaps it in; the caption says "100%" only once those are on screen.
Pictures are shown turned by rotation_of(i) quarter turns (thumbnails, the zoom page); caches stay unrotated.

Thumbnails come from ``prefetcher.cached(i)`` when it has the picture, else from a small decode on a
QThreadPool (at most ``MAX_INFLIGHT`` queued at once, 2 threads), cached per index.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QPointF, QRect, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import (QImage, QKeyEvent, QPainter, QPixmap, QStandardItem, QStandardItemModel,
                           QTransform)
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from spuza import features
from spuza.copy import human_size
from spuza.reveal import reveal_menu
from spuza.ui import theme
from spuza.ui.viewer import info_native_size, step_scale

THUMB_SIDE = 480  # decode size for the worker
CELL = QSize(212, 176)
THUMB_BOX = QSize(196, 130)
ZOOM_SIDE = 2560
NATIVE = 0  # the "side" of a native-size decode (the loader gets max_side None)
MAX_INFLIGHT = 8  # decodes queued at once
IDX_ROLE = Qt.ItemDataRole.UserRole + 1
KIND_ROLE = Qt.ItemDataRole.UserRole + 2

PAGE_EMPTY, PAGE_GRID, PAGE_ZOOM = 0, 1, 2


def percent(n: int, total: int) -> int:
    """Whole percent of n in total, rounded DOWN (19/37 = 51.35 -> 51); 0 when total is 0."""
    return n * 100 // total if total > 0 else 0


def header_text(marked: int, total: int, nbytes: int) -> str:
    return f"{marked} marked of {total} · {human_size(nbytes)}"


def _single_raw(kind: str) -> bool:
    return kind.endswith(" only")


class _Signals(QObject):
    done = Signal(int, object, int, str)  # index, QImage|None, max_side, error


class _Job(QRunnable):
    def __init__(self, sig: _Signals, loader: Callable, i: int, path, max_side: int):
        super().__init__()
        self.sig, self.loader, self.i, self.path, self.max_side = sig, loader, i, path, max_side
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            img, _info = self.loader(self.path, self.max_side or None)
            rgb = img.convert("RGB")
            w, h = rgb.size
            q = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        except Exception as exc:  # noqa: BLE001 - a bad picture shows a placeholder, never raises
            self.sig.done.emit(self.i, None, self.max_side, str(exc) or type(exc).__name__)
            return
        self.sig.done.emit(self.i, q, self.max_side, "")


class _Delegate(QStyledItemDelegate):
    def __init__(self, grid: ReviewGrid):
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option, index):  # noqa: N802
        return CELL

    def paint(self, p: QPainter, option, index) -> None:
        p.save()
        pal = option.palette
        r = option.rect.adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            p.fillRect(option.rect, pal.highlight())
        text_col = pal.highlightedText().color() if selected else pal.text().color()
        box = QRect(r.x() + (r.width() - THUMB_BOX.width()) // 2, r.y(), THUMB_BOX.width(), THUMB_BOX.height())
        pm = self.grid._shown_pixmap(index.data(IDX_ROLE))
        if pm is None:
            p.fillRect(box, pal.alternateBase())
            p.setPen(pal.placeholderText().color())
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, "…")
        else:
            p.drawPixmap(box.x() + (box.width() - pm.width()) // 2, box.y() + (box.height() - pm.height()) // 2, pm)
        kind = index.data(KIND_ROLE)
        p.setPen(text_col)
        fm = p.fontMetrics()
        name = fm.elidedText(index.data(Qt.ItemDataRole.DisplayRole), Qt.TextElideMode.ElideMiddle, r.width())
        p.drawText(QRect(r.x(), box.bottom() + 6, r.width(), fm.height()), Qt.AlignmentFlag.AlignLeft, name)
        badge_rect = QRect(r.x(), box.bottom() + 8 + fm.height(), fm.horizontalAdvance(kind) + 14, fm.height() + 2)
        if _single_raw(kind):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(theme.current().warning))
            p.drawRoundedRect(badge_rect, 4, 4)
            p.setPen(theme.qcolor(theme.current().hud, 255))
        else:
            p.setPen(text_col)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(badge_rect, 4, 4)
        p.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, kind)
        p.restore()


class ReviewGrid(QWidget):
    open_index = Signal(int)
    marks_changed = Signal()
    closed = Signal()

    def __init__(self, catalog, prefetcher=None, parent=None, loader: Callable | None = None):
        super().__init__(parent)
        self.catalog, self.prefetcher = catalog, prefetcher
        self._loader = loader
        self.rotation_of = lambda i: 0  # contract fr3: clockwise quarter turns per index; the window assigns it
        self._thumbs: dict[int, QPixmap] = {}
        self._failed: set[int] = set()
        self._queue: list[int] = []
        self._inflight: set[int] = set()
        self._sig = _Signals()
        self._sig.done.connect(self._on_loaded)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(2)
        self._zoom_index: int | None = None
        self._zoom_fit = True
        self._zoom_scale = 1.0  # while not fit: picture pixels per device pixel (1.0 = Z's 100%)
        self._zoom_pct = False  # the level was chosen with + / -: the caption shows its percentage
        self._zoom_loading: set[int] = set()  # ZOOM_SIDE decodes in flight
        self._zoom_big: dict[int, QImage] = {}
        self._native_img: tuple[int, QImage] | None = None  # the one native-size decode held (96 MB at 24 MP)
        self._native_loading: set[int] = set()
        self._native_failed: set[int] = set()
        self._zoom_native = False  # the image in the zoom page is the native pixels
        self._turned: dict[tuple[int, int], QPixmap] = {}  # (index, quarters) -> rotated thumbnail

        self.header = QLabel()
        self.unmark_button = QPushButton("Unmark")
        self.unmark_button.setToolTip("Take this picture off the marked list (nothing is deleted)")
        self.zoom_button = QPushButton("Zoom")
        for b in (self.unmark_button, self.zoom_button):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.unmark_button.clicked.connect(self.unmark_selected)
        self.zoom_button.clicked.connect(self.toggle_zoom)
        top = QHBoxLayout()
        top.addWidget(self.header, 1)
        top.addWidget(self.zoom_button)
        top.addWidget(self.unmark_button)

        self.model = QStandardItemModel(self)
        self.view = QListView()
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setUniformItemSizes(True)
        self._multi: bool | None = None
        self._apply_selection_mode()
        self.view.setGridSize(CELL)
        self.view.setModel(self.model)
        self.view.setItemDelegate(_Delegate(self))
        self.view.doubleClicked.connect(lambda _i: self.show_zoom())
        self.view.installEventFilter(self)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(lambda pos: self._popup(self.view, pos, False))

        self.empty = QLabel("Nothing marked yet")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.scene = QGraphicsScene(self)
        self.zoom_view = QGraphicsView(self.scene)
        self.zoom_view.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.zoom_view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.zoom_view.installEventFilter(self)
        self.zoom_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.zoom_view.customContextMenuRequested.connect(lambda pos: self._popup(self.zoom_view, pos, True))
        self._zoom_item: QGraphicsPixmapItem | None = None
        self.zoom_caption = QLabel()
        zoom_page = QWidget()
        zl = QVBoxLayout(zoom_page)
        zl.setContentsMargins(0, 0, 0, 0)
        zl.addWidget(self.zoom_view, 1)
        zl.addWidget(self.zoom_caption)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.view)
        self.stack.addWidget(zoom_page)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.stack, 1)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if prefetcher is not None and hasattr(prefetcher, "ready"):
            prefetcher.ready.connect(self._on_prefetched)
        self.refresh()

    # ---- model -------------------------------------------------------------------------------
    def marked_indices(self) -> list[int]:
        c = self.catalog
        return [i for i in range(len(c.pictures)) if c.is_marked(i)]

    def _apply_selection_mode(self) -> None:
        """Advanced + compare_view: Ctrl/Cmd-click selects several (for compare). Classic stays single."""
        multi = features.enabled("compare_view")
        if multi != self._multi:
            self._multi = multi
            self.view.setSelectionMode(
                QListView.SelectionMode.ExtendedSelection if multi else QListView.SelectionMode.SingleSelection
            )

    def refresh(self) -> None:
        self._apply_selection_mode()
        keep = self.selected_index()
        keep_row = self.view.currentIndex().row()
        self.model.clear()
        for i in self.marked_indices():
            pic = self.catalog.pictures[i]
            it = QStandardItem(pic.stem)
            it.setData(i, IDX_ROLE)
            it.setData(pic.kind, KIND_ROLE)
            it.setEditable(False)
            it.setToolTip(f"{pic.primary.name} · {pic.kind} · {human_size(pic.size_bytes)}")
            self.model.appendRow(it)
        n = self.model.rowCount()
        self.header.setText(header_text(self.catalog.marked_count, len(self.catalog), self.catalog.marked_bytes))
        self.header.setToolTip(f"{percent(self.catalog.marked_count, len(self.catalog))}% of the card is marked")
        self.unmark_button.setEnabled(n > 0)
        self.zoom_button.setEnabled(n > 0)
        if self._zoom_index is not None and not self.catalog.is_marked(self._zoom_index):
            self._zoom_index = None
        if self._zoom_index is not None:
            self.stack.setCurrentIndex(PAGE_ZOOM)
        else:
            self.stack.setCurrentIndex(PAGE_GRID if n else PAGE_EMPTY)
        if n:
            row = self._row_of(keep) if keep is not None else -1
            if row < 0:  # the selected one went away: stay at the same place
                row = min(max(keep_row, 0), n - 1)
            self.view.setCurrentIndex(self.model.index(row, 0))
        self._queue = [i for i in self.marked_indices() if self._pixmap(i) is None and i not in self._failed]
        self._pump()

    def _row_of(self, idx: int) -> int:
        for r in range(self.model.rowCount()):
            if self.model.item(r).data(IDX_ROLE) == idx:
                return r
        return -1

    def selected_index(self) -> int | None:
        cur = self.view.currentIndex()
        return cur.data(IDX_ROLE) if cur.isValid() else None

    def selected_indices(self) -> list[int]:
        """Catalog indices of every selected picture in grid order (the current one alone in classic)."""
        if not features.enabled("compare_view") or not self._multi:
            i = self.selected_index()
            return [] if i is None else [i]
        rows = sorted(ix.row() for ix in self.view.selectionModel().selectedIndexes())
        got = [self.model.item(r).data(IDX_ROLE) for r in rows]
        cur = self.selected_index()
        if not got and cur is not None:
            got = [cur]
        return got

    # ---- thumbnails ----------------------------------------------------------------------------
    def _pixmap(self, i: int) -> QPixmap | None:
        pm = self._thumbs.get(i)
        if pm is not None:
            return pm
        img = self.prefetcher.cached(i) if self.prefetcher is not None else None
        if img is not None and not img.isNull():
            pm = QPixmap.fromImage(
                img.scaled(THUMB_BOX, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
            self._thumbs[i] = pm
            return pm
        return None

    def _quarters(self, i) -> int:
        try:
            return int(self.rotation_of(i)) % 4
        except Exception:  # noqa: BLE001 - a bad rotation provider shows the picture unrotated
            return 0

    def _shown_pixmap(self, i: int) -> QPixmap | None:
        """The thumbnail as shown: turned by the picture's rotation, fitted back into the thumbnail box."""
        pm = self._pixmap(i)
        q = self._quarters(i)
        if pm is None or q == 0:
            return pm
        got = self._turned.get((i, q))
        if got is None:
            turned = pm.transformed(QTransform().rotate(90.0 * q), Qt.TransformationMode.SmoothTransformation)
            got = turned.scaled(THUMB_BOX, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._turned[(i, q)] = got
        return got

    def _get_loader(self) -> Callable:
        if self._loader is None:
            from spuza.preview import load  # lazy: keeps decoders out of module import

            self._loader = load
        return self._loader

    def _native_loader(self, sidecar=None) -> Callable:
        if self._loader is not None:  # an injected loader is called as loader(path, None) for native
            return self._loader
        from spuza.preview import load_native

        return lambda path, _max_side: load_native(path, sidecar)

    def _pump(self) -> None:
        while self._queue and len(self._inflight) < MAX_INFLIGHT:
            i = self._queue.pop(0)
            if i in self._inflight or i in self._thumbs or not self.catalog.is_marked(i):
                continue
            self._inflight.add(i)
            self._pool.start(_Job(self._sig, self._get_loader(), i, self.catalog.pictures[i].primary, THUMB_SIDE))

    def _on_loaded(self, i: int, img, max_side: int, err: str) -> None:
        if max_side == NATIVE:
            self._native_loading.discard(i)
            if img is None or img.isNull():
                self._native_failed.add(i)
            else:
                self._native_img = (i, img)
                if self._zoom_index == i and not self._zoom_fit:
                    self._set_zoom_image(img, native=True)
            if self._zoom_index == i:
                self._update_caption()
            return
        if max_side == ZOOM_SIDE:
            self._zoom_loading.discard(i)
            if img is not None and not img.isNull():
                self._zoom_big[i] = img
                if self._zoom_index == i and not (self._zoom_native and not self._zoom_fit):
                    self._set_zoom_image(img)  # never over the native pixels already on screen
            return
        self._inflight.discard(i)
        if img is None or img.isNull():
            self._failed.add(i)
        else:
            self._thumbs[i] = QPixmap.fromImage(
                img.scaled(THUMB_BOX, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
            self.view.viewport().update()
        self._pump()

    def _on_prefetched(self, i: int, _img=None, _info=None) -> None:
        if i not in self._thumbs and self.catalog.is_marked(i):
            self.view.viewport().update()

    def wait_idle(self, timeout_ms: int = 5000) -> bool:
        """Block until running decodes finish (tests, shutdown); bounded by timeout_ms."""
        return self._pool.waitForDone(timeout_ms)

    # ---- actions -------------------------------------------------------------------------------
    def unmark_selected(self) -> None:
        if self.stack.currentIndex() == PAGE_ZOOM:
            picks = [] if self._zoom_index is None else [self._zoom_index]
        else:
            picks = self.selected_indices()
        if not picks:
            return
        for i in picks:
            self.catalog.set_mark(i, False)
        self._zoom_index = None
        self.refresh()
        self.marks_changed.emit()

    def show_zoom(self) -> None:
        i = self.selected_index()
        if i is None:
            return
        self._zoom_index, self._zoom_fit, self._zoom_pct = i, True, False
        self.stack.setCurrentIndex(PAGE_ZOOM)
        self._update_caption()
        img = self._zoom_big.get(i)
        if self._native_img is not None and self._native_img[0] != i:
            self._native_img = None  # one native decode at a time
        if img is None and self.prefetcher is not None:
            img = self.prefetcher.cached(i)
        if img is None:
            th = self._thumbs.get(i)
            img = th.toImage() if th is not None else None
        if img is not None:
            self._set_zoom_image(img)
        else:  # nothing for this picture yet: do not leave the previous one under the new caption
            self.scene.clear()
            self._zoom_item = None
            self._zoom_native = False
        if i not in self._zoom_big and i not in self._zoom_loading:
            self._zoom_loading.add(i)
            self._pool.start(_Job(self._sig, self._get_loader(), i, self.catalog.pictures[i].primary, ZOOM_SIDE))
        self.zoom_view.setFocus()

    def _update_caption(self) -> None:
        i = self._zoom_index
        if i is None:
            return
        pic = self.catalog.pictures[i]
        state = ""
        lab = self.zoom_label()
        if not self._zoom_fit:
            if self._zoom_native:
                state = f" · {lab}"
            elif i in self._native_failed:
                state = " · preview pixels only (full decode failed)"
            else:
                state = f" · {lab} … decoding full resolution…"
        self.zoom_caption.setText(
            f"{pic.primary.name} · {pic.kind} · {human_size(pic.size_bytes)}{state}"
            "   (Z: fit / 100%, + / -: zoom, M: unmark, Esc: back)"
        )

    def zoom_label(self) -> str:
        """"100%" for Z; the level's own percentage once + or - chose it."""
        return f"{int(round(self._zoom_scale * 100))}%" if self._zoom_pct and not self._zoom_fit else "100%"

    def _centre_frac(self):
        """The centre of the zoom page as a fraction of the picture, in its own (unrotated) frame."""
        if self._zoom_item is None:
            return None
        pm = self._zoom_item.pixmap()
        c = self._zoom_item.mapFromScene(self.zoom_view.mapToScene(self.zoom_view.viewport().rect().center()))
        return (c.x() / max(1, pm.width()), c.y() / max(1, pm.height()))

    def _orient_item(self) -> None:
        if self._zoom_item is None:
            return
        pm = self._zoom_item.pixmap()
        self._zoom_item.setTransformOriginPoint(pm.width() / 2, pm.height() / 2)
        self._zoom_item.setRotation(90.0 * self._quarters(self._zoom_index))
        self.scene.setSceneRect(self._zoom_item.sceneBoundingRect())

    def _set_zoom_image(self, img: QImage, native: bool = False) -> None:
        frac = self._centre_frac() if not self._zoom_fit else None  # a swap keeps the centred point
        self.scene.clear()
        self._zoom_native = native
        self._zoom_item = self.scene.addPixmap(QPixmap.fromImage(img))
        self._orient_item()
        self._apply_zoom(frac)

    def _apply_zoom(self, frac=None) -> None:
        self.zoom_view.resetTransform()
        if self._zoom_fit and self._zoom_item is not None:
            self.zoom_view.fitInView(self._zoom_item, Qt.AspectRatioMode.KeepAspectRatio)
        elif self._zoom_item is not None:
            dpr = self.zoom_view.devicePixelRatioF()
            # 100% means one picture pixel per device pixel; a smaller picture on screen than the native one
            # is drawn at the native size, so the swap to the native pixels does not jump
            t = self._zoom_scale * self._preview_ratio() / dpr
            if abs(t - 1.0) > 1e-9:
                self.zoom_view.scale(t, t)
            if frac is None:
                frac = self._focus_frac(self._zoom_index)  # advanced + focus_point: 100% lands on the AF point
            if frac is not None:
                pm = self._zoom_item.pixmap()
                self.zoom_view.centerOn(self._zoom_item.mapToScene(QPointF(frac[0] * pm.width(), frac[1] * pm.height())))

    def _native_size(self):
        """The zoom page picture's native (w, h) in its pixmap's frame; None with no picture on screen."""
        if self._zoom_item is None:
            return None
        pm = self._zoom_item.pixmap()
        shown = (pm.width(), pm.height())
        if self._zoom_native:
            return shown
        i = self._zoom_index
        if self._native_img is not None and self._native_img[0] == i:
            return (self._native_img[1].width(), self._native_img[1].height())
        try:
            info = self.prefetcher.info(i) if self.prefetcher is not None else None
        except Exception:  # noqa: BLE001 - a bad info provider just means the pixmap size is used
            info = None
        return info_native_size(info, shown) or shown

    def _preview_ratio(self) -> float:
        nat = self._native_size()
        if nat is None or self._zoom_item is None or self._zoom_item.pixmap().width() <= 0:
            return 1.0
        return nat[0] / self._zoom_item.pixmap().width()

    def _fit_scale(self) -> float:
        nat = self._native_size()
        if nat is None:
            return 1.0
        w, h = (nat[1], nat[0]) if self._quarters(self._zoom_index) % 2 else nat
        vp = self.zoom_view.viewport()
        return min(vp.width() / max(1, w), vp.height() / max(1, h)) * self.zoom_view.devicePixelRatioF()

    def zoom_step(self, up: bool) -> None:
        """+ (up) or - (down) on the zoom page: the next 25% level; - from the lowest goes back to fit. The grid
        page ignores both."""
        if self.stack.currentIndex() != PAGE_ZOOM or self._zoom_item is None:
            return
        if self._zoom_fit and not up:
            return
        fit = self._fit_scale()
        new = step_scale(fit if self._zoom_fit else self._zoom_scale, fit, up)
        if new is None:
            return
        was_fit = self._zoom_fit
        frac = None if was_fit else self._centre_frac()
        if new == 0.0:
            self._zoom_fit, self._zoom_pct = True, False
            self._apply_zoom()
        else:
            self._zoom_fit, self._zoom_scale, self._zoom_pct = False, new, True
            self._apply_zoom(frac)
            if was_fit:
                self._want_native()
        self._update_caption()

    def _want_native(self) -> None:
        """Going to 100%: put the native pixels on screen, loading them once if they are not held yet."""
        i = self._zoom_index
        if i is None:
            return
        if self._native_img is not None and self._native_img[0] == i:
            if not self._zoom_native:
                self._set_zoom_image(self._native_img[1], native=True)
        elif i not in self._native_loading and i not in self._native_failed:
            self._native_loading.add(i)
            self._pool.start(_Job(self._sig, self._native_loader(self.catalog.pictures[i].sidecar), i,
                                      self.catalog.pictures[i].primary, NATIVE))

    def rotation_changed(self, i: int) -> None:
        """Picture i's rotation changed: repaint its thumbnail; if it is the zoom page's, turn it in place."""
        for key in [k for k in self._turned if k[0] == i]:
            del self._turned[key]
        self.view.viewport().update()
        if self._zoom_index == i and self._zoom_item is not None:
            frac = self._centre_frac() if not self._zoom_fit else None
            self._orient_item()
            self._apply_zoom(frac)

    def _focus_frac(self, i):
        """The picture's AF point as a (fx, fy) fraction, or None (flag off, no info, no usable point)."""
        if i is None or self.prefetcher is None or not features.enabled("focus_point"):
            return None
        try:
            info = self.prefetcher.info(i)
            fi = getattr(info, "focus", None)
            return fi.frac() if fi is not None else None
        except Exception:  # noqa: BLE001 - a bad point must never break Z
            return None

    def toggle_zoom(self) -> None:
        if self.stack.currentIndex() != PAGE_ZOOM:
            self.show_zoom()
        else:
            self._zoom_fit = not self._zoom_fit
            self._zoom_scale, self._zoom_pct = 1.0, False
            self._apply_zoom()
            if not self._zoom_fit:
                self._want_native()
            self._update_caption()

    def leave_zoom(self) -> None:
        self._zoom_index = None
        self.stack.setCurrentIndex(PAGE_GRID if self.model.rowCount() else PAGE_EMPTY)
        self.view.setFocus()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if self.stack.currentIndex() == PAGE_ZOOM and self._zoom_fit:
            self._apply_zoom()

    # ---- right-click: reveal the picture in the file manager -----------------------------------
    def context_menu_for(self, pos, zoom: bool = False):
        """The menu for a right-click at `pos` (in the grid's viewport, or the zoom page), or None."""
        if zoom:
            i = self._zoom_index
        else:
            ix = self.view.indexAt(pos)
            i = ix.data(IDX_ROLE) if ix.isValid() else None
        if i is None:
            return None
        return reveal_menu(self, self.catalog.pictures[i])

    def _popup(self, widget, pos, zoom: bool) -> None:
        menu = self.context_menu_for(pos, zoom)
        if menu is not None:
            menu.exec(widget.mapToGlobal(pos))

    # ---- keys ----------------------------------------------------------------------------------
    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if isinstance(ev, QKeyEvent) and ev.type() == ev.Type.KeyPress and obj in (self.view, self.zoom_view):
            return self._handle_key(ev)
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        if not self._handle_key(ev):
            super().keyPressEvent(ev)

    def _handle_key(self, ev: QKeyEvent) -> bool:
        k = ev.key()
        if ev.isAutoRepeat() and k in (Qt.Key.Key_M, Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return True  # a held key must not unmark a run of pictures or reopen repeatedly
        zooming = self.stack.currentIndex() == PAGE_ZOOM
        if k in (Qt.Key.Key_M, Qt.Key.Key_Space):  # never Delete: nothing is deleted
            self.unmark_selected()
        elif k == Qt.Key.Key_Z:
            self.toggle_zoom()
        elif k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_step(True)
        elif k == Qt.Key.Key_Minus:
            self.zoom_step(False)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            i = self._zoom_index if zooming else self.selected_index()
            if i is not None:
                self.open_index.emit(i)
        elif k == Qt.Key.Key_Escape:
            if zooming:
                self.leave_zoom()
            else:
                self.closed.emit()
        else:
            return False
        return True

    def closeEvent(self, ev) -> None:  # noqa: N802
        self._queue.clear()
        self._pool.clear()
        self._pool.waitForDone(2000)
        super().closeEvent(ev)
