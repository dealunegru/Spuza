"""Review grid: the marked pictures as thumbnails; unmark, zoom, open one in the cull view.

Keys: M or Space unmarks the selected picture (Delete is deliberately NOT bound: nothing is ever
deleted), Z or double-click zooms (Z again toggles fit / 100%), Return/Enter emits ``open_index``,
Esc leaves zoom, or on the grid emits ``closed``.

Thumbnails come from ``prefetcher.cached(i)`` when it has the picture, else from a small decode on a
QThreadPool (at most ``MAX_INFLIGHT`` queued at once, 2 threads), cached per index.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRect, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPixmap, QStandardItem, QStandardItemModel
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

from spuza.copy import human_size
from spuza.reveal import reveal_menu

THUMB_SIDE = 480  # decode size for the worker
CELL = QSize(212, 176)
THUMB_BOX = QSize(196, 130)
ZOOM_SIDE = 2560
MAX_INFLIGHT = 8  # decodes queued at once
AMBER = QColor("#d98a00")
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
            img, _info = self.loader(self.path, self.max_side)
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
        pm = self.grid._pixmap(index.data(IDX_ROLE))
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
            p.setBrush(AMBER)
            p.drawRoundedRect(badge_rect, 4, 4)
            p.setPen(QColor("black"))
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
        self._zoom_loading: set[int] = set()  # ZOOM_SIDE decodes in flight
        self._zoom_big: dict[int, QImage] = {}

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
        self.view.setSelectionMode(QListView.SelectionMode.SingleSelection)
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

    def refresh(self) -> None:
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

    def _get_loader(self) -> Callable:
        if self._loader is None:
            from spuza.preview import load  # lazy: keeps decoders out of module import

            self._loader = load
        return self._loader

    def _pump(self) -> None:
        while self._queue and len(self._inflight) < MAX_INFLIGHT:
            i = self._queue.pop(0)
            if i in self._inflight or i in self._thumbs or not self.catalog.is_marked(i):
                continue
            self._inflight.add(i)
            self._pool.start(_Job(self._sig, self._get_loader(), i, self.catalog.pictures[i].primary, THUMB_SIDE))

    def _on_loaded(self, i: int, img, max_side: int, err: str) -> None:
        if max_side == ZOOM_SIDE:
            self._zoom_loading.discard(i)
            if img is not None and not img.isNull():
                self._zoom_big[i] = img
                if self._zoom_index == i:
                    self._set_zoom_image(img)
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
        i = self._zoom_index if self.stack.currentIndex() == PAGE_ZOOM else self.selected_index()
        if i is None:
            return
        self.catalog.set_mark(i, False)
        self._zoom_index = None
        self.refresh()
        self.marks_changed.emit()

    def show_zoom(self) -> None:
        i = self.selected_index()
        if i is None:
            return
        self._zoom_index, self._zoom_fit = i, True
        self.stack.setCurrentIndex(PAGE_ZOOM)
        pic = self.catalog.pictures[i]
        self.zoom_caption.setText(
            f"{pic.primary.name} · {pic.kind} · {human_size(pic.size_bytes)}"
            "   (Z: fit / 100%, M: unmark, Esc: back)"
        )
        img = self._zoom_big.get(i)
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
        if i not in self._zoom_big and i not in self._zoom_loading:
            self._zoom_loading.add(i)
            self._pool.start(_Job(self._sig, self._get_loader(), i, self.catalog.pictures[i].primary, ZOOM_SIDE))
        self.zoom_view.setFocus()

    def _set_zoom_image(self, img: QImage) -> None:
        self.scene.clear()
        self._zoom_item = self.scene.addPixmap(QPixmap.fromImage(img))
        self.scene.setSceneRect(self._zoom_item.boundingRect())
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        self.zoom_view.resetTransform()
        if self._zoom_fit and self._zoom_item is not None:
            self.zoom_view.fitInView(self._zoom_item, Qt.AspectRatioMode.KeepAspectRatio)

    def toggle_zoom(self) -> None:
        if self.stack.currentIndex() != PAGE_ZOOM:
            self.show_zoom()
        else:
            self._zoom_fit = not self._zoom_fit
            self._apply_zoom()

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
