"""Horizontal filmstrip. A QListView over a model, so only the visible items are ever painted."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate

from spuza.ui import theme

CELL_W, CELL_H = 112, 84
THUMB_W, THUMB_H = 104, 62
MEMO_MAX = 600  # scaled thumbnails kept; oldest dropped first
AHEAD_BAND = 12  # how many of the next pictures the decoded-ahead band looks at


class _Model(QAbstractListModel):
    def __init__(self, strip: Filmstrip):
        super().__init__(strip)
        self.strip = strip

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.strip.catalog)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and index.isValid():
            return self.strip.catalog.pictures[index.row()].stem
        return None

    def touch(self, lo: int, hi: int) -> None:
        n = self.rowCount()
        lo, hi = max(0, lo), min(n - 1, hi)
        if lo <= hi:
            self.dataChanged.emit(self.index(lo), self.index(hi))


class _Delegate(QStyledItemDelegate):
    def __init__(self, strip: Filmstrip):
        super().__init__(strip)
        self.strip = strip

    def sizeHint(self, option, index) -> QSize:
        return QSize(CELL_W, CELL_H)

    def paint(self, p: QPainter, option, index) -> None:
        s, c, i = self.strip, theme.current(), index.row()
        pic = s.catalog.pictures[i]
        cell: QRect = option.rect.adjusted(4, 4, -4, -4)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        thumb = s.thumb(i)
        box = QRect(cell.x(), cell.y(), THUMB_W, THUMB_H)
        if thumb is None:
            p.fillRect(box, QColor(c.border))
        else:
            p.fillRect(box, QColor(c.stage))
            p.drawPixmap(box.x() + (THUMB_W - thumb.width()) // 2, box.y() + (THUMB_H - thumb.height()) // 2,
                         thumb)
        if i == s.current:
            p.setPen(QPen(QColor(c.accent), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(box.adjusted(1, 1, -1, -1))
        # decoded-ahead band under the thumbnail
        if s.is_ready_ahead(i):
            p.fillRect(QRect(box.x(), box.bottom() + 1, box.width(), 3), QColor(c.green))
        # kind badge
        amber = "only" in pic.kind
        f = QFont(p.font())
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(pic.kind) + 8
        badge = QRect(box.x() + 2, box.bottom() - 15, tw, 13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.amber if amber else "#000000"))
        if not amber:
            p.setOpacity(0.6)
        p.drawRoundedRect(badge, 3, 3)
        p.setOpacity(1.0)
        p.setPen(QColor("#1a1000" if amber else "#ffffff"))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, pic.kind)
        # mark check
        if s.catalog.is_marked(i):
            dot = QRect(box.right() - 17, box.y() + 3, 14, 14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c.green))
            p.drawEllipse(dot)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(dot.x() + 3, dot.y() + 7, dot.x() + 6, dot.y() + 10)
            p.drawLine(dot.x() + 6, dot.y() + 10, dot.x() + 11, dot.y() + 4)
        p.restore()


class Filmstrip(QListView):
    picked = Signal(int)

    def __init__(self, catalog, prefetcher=None, parent=None):
        super().__init__(parent)
        self.catalog, self.prefetcher, self.current = catalog, prefetcher, 0
        self._memo: dict[int, QPixmap] = {}
        self._model = _Model(self)
        self.setModel(self._model)
        self.setItemDelegate(_Delegate(self))
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setFixedHeight(CELL_H + 18)
        self.clicked.connect(lambda ix: self.picked.emit(ix.row()))

    # -- data the delegate reads ------------------------------------------------------------------
    def thumb(self, i: int) -> QPixmap | None:
        got = self._memo.get(i)
        if got is not None:
            return got
        img = self.prefetcher.cached(i) if self.prefetcher is not None else None
        if img is None or img.isNull():
            return None
        big = img.scaled(THUMB_W * 3, THUMB_H * 3, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.FastTransformation)
        pm = QPixmap.fromImage(big.scaled(THUMB_W, THUMB_H, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        self._memo[i] = pm
        while len(self._memo) > MEMO_MAX:
            del self._memo[next(iter(self._memo))]
        return pm

    def is_ready_ahead(self, i: int) -> bool:
        return (self.prefetcher is not None and self.current < i <= self.current + AHEAD_BAND
                and self.prefetcher.cached(i) is not None)

    # -- updates from the window ------------------------------------------------------------------
    def set_current(self, i: int) -> None:
        old, self.current = self.current, i
        self._model.touch(old, old)
        self._model.touch(i, i + AHEAD_BAND)
        if len(self.catalog):
            self.scrollTo(self._model.index(i), QAbstractItemView.ScrollHint.PositionAtCenter)

    def item_changed(self, i: int) -> None:
        """A thumbnail arrived or a mark flipped."""
        self._model.touch(i, i)

    def retheme(self) -> None:
        self.viewport().update()
