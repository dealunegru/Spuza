"""Horizontal filmstrip. A QListView over a model, so only the visible items are ever painted.

Friend round 7: the rows are ``band.rows(len(catalog))``: an original, then each derived picture (a crop or story fit)
made from it. A derived cell's thumbnail is cut, in memory, from its source's thumbnail; if that is not cached yet the
cell is a placeholder and the source is requested. Nothing here reads a file on the UI thread."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate

from spuza import derived as dv
from spuza.reveal import reveal_menu
from spuza.ui import theme
from spuza.ui.pill_tray import draw_dots_right
from spuza.ui.star_bar import clamp_stars, draw_star
from spuza.ui.thumbs import ThumbLoader, cut_crop, story_frame

CELL_W, CELL_H = 112, 84
THUMB_W, THUMB_H = 104, 62
MEMO_MAX = 600  # scaled thumbnails kept; oldest dropped first
STRIP_DOTS = 4  # pill dots drawn on a thumbnail (a "+" after that); the badge sits bottom left
BAND_H = 14  # the free band under the thumb (the cell is CELL_H - 8 high inside its margin)
STAR_R, STAR_PITCH = 5.0, 11  # the rating's stars in that band
SCROLL_DEBOUNCE_MS = 150  # a scroll or resize settles this long before the strip asks for thumbnails


class _Model(QAbstractListModel):
    def __init__(self, strip: Filmstrip):
        super().__init__(strip)
        self.strip = strip

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.strip.rows())

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        rows = self.strip.rows()
        if role == Qt.ItemDataRole.DisplayRole and index.isValid() and 0 <= index.row() < len(rows):
            return self.strip.catalog.pictures[rows[index.row()][0]].stem
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
        s, c = self.strip, theme.current()
        rows = s.rows()
        if not 0 <= index.row() < len(rows):
            return
        i, did = rows[index.row()]  # the picture's catalog index, and the derived id (None on an original)
        d = s.band.get(did) if did is not None else None
        pic = s.catalog.pictures[i]
        cell: QRect = option.rect.adjusted(4, 4, -4, -4)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        thumb = s.thumb(i) if d is None else s.derived_thumb(d)
        box = QRect(cell.x(), cell.y(), THUMB_W, THUMB_H)
        if thumb is None:
            p.fillRect(box, QColor(c.border))
        else:
            p.fillRect(box, QColor(c.stage))
            lw, lh = thumb.deviceIndependentSize().toSize().toTuple()  # logical size: the pixmap has a DPR
            p.drawPixmap(box.x() + (THUMB_W - lw) // 2, box.y() + (THUMB_H - lh) // 2, thumb)
        if i in s.highlight and d is None:
            p.setPen(QPen(QColor(c.text), 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(cell.adjusted(-2, -2, 2, 2))
        if i == s.current and did == s.current_id:
            p.setPen(QPen(QColor(c.text), 3))  # neutral: the picture is the colour here
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(box.adjusted(1, 1, -1, -1))
        f = QFont(p.font())
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        if d is None:  # kind badge (a derived picture is a JPEG of its own: its tag says what it is)
            amber = "only" in pic.kind
            tw = p.fontMetrics().horizontalAdvance(pic.kind) + 8
            badge = QRect(box.x() + 2, box.bottom() - 15, tw, 13)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(c.warning) if amber else theme.qcolor(c.hud, 255))
            if not amber:
                p.setOpacity(0.6)
            p.drawRoundedRect(badge, 3, 3)
            p.setOpacity(1.0)
            p.setPen(theme.qcolor(c.hud, 255) if amber else QColor(c.on_focus))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, pic.kind)
        tx = box.x() + 2  # the edit tags, top left, in the kind badge's style, side by side
        for text, kind in (s.edit_tags(i) if d is None else s.derived_tags(d)):
            tw = p.fontMetrics().horizontalAdvance(text) + 8
            tb = QRect(tx, box.y() + 3, tw, 13)
            if kind == "story":  # an outline: the story fit is a different edit from the crop
                p.setPen(QPen(QColor(c.focus), 1))
                p.setBrush(QColor(c.panel))
                p.drawRoundedRect(tb.adjusted(0, 0, -1, -1), 3, 3)
                p.setPen(QColor(c.focus))
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(c.focus))
                p.drawRoundedRect(tb, 3, 3)
                p.setPen(QColor(c.on_focus))
            p.drawText(tb, Qt.AlignmentFlag.AlignCenter, text)
            tx += tw + 3
        if d is None:  # the picture's pills: dots, bottom right (the mark check owns the top right)
            names = s.pill_colors(i)
            if names:
                draw_dots_right(p, box.right() - 3, box.bottom() - 9, names, most=STRIP_DOTS)
        if d is None:  # the rating: small filled stars in the free band under the thumb, at the left
            n = s.star_count(i)
            for k in range(n):
                draw_star(p, box.x() + 2 + STAR_R + k * STAR_PITCH, box.bottom() + 1 + BAND_H / 2, STAR_R, True,
                          QColor(c.text))
        # mark check
        if s.catalog.is_marked(i) if d is None else d.marked:
            dot = QRect(box.right() - 17, box.y() + 3, 14, 14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c.marked))
            p.drawEllipse(dot)
            p.setPen(QPen(QColor(c.on_focus), 2))
            p.drawLine(dot.x() + 3, dot.y() + 7, dot.x() + 6, dot.y() + 10)
            p.drawLine(dot.x() + 6, dot.y() + 10, dot.x() + 11, dot.y() + 4)
        p.restore()


class Filmstrip(QListView):
    picked = Signal(int)  # an original's cell was clicked: its catalog index
    picked_derived = Signal(int, int)  # a derived picture's cell was clicked: its source's catalog index, its id

    def __init__(self, catalog, prefetcher=None, parent=None, band=None):
        super().__init__(parent)
        self.catalog, self.prefetcher, self.current = catalog, prefetcher, 0
        self.current_id: int | None = None  # the current cell's derived id (None: the original `current`)
        self.band = band if band is not None else dv.Band()  # shared by reference with the window
        self.rotation_of = lambda i: 0  # contract fr3: clockwise quarter turns per index; the window assigns it
        # friend round 5/6: per index a list of (text, kind) tags for an ORIGINAL, kind "crop" (accent fill) or
        # "story" (accent outline); [] for none. A bare string is one crop tag. Round 7: originals carry none, the
        # window leaves this alone; a derived picture's tag comes from `derived.tag`.
        self.edit_tag_of = lambda i: []
        self.pills_of = lambda i: ()  # the window assigns it: catalog index -> the pill colour NAMES to draw as dots
        self.stars_of = lambda i: 0  # the window assigns it: catalog index -> the star rating 0..5
        self._memo: dict[tuple[int, int], QPixmap] = {}  # (index, quarter turns) -> scaled, rotated pixmap
        # derived id -> (content key, pixmap): ONE entry per derived picture, so a changed item drops its old cut
        self._dmemo: dict[int, tuple[tuple, QPixmap]] = {}
        self._dver = self.band.version
        self._nrows = len(self.band.rows(len(catalog)))  # the row count the model last announced
        self._loader = ThumbLoader(lambda i: self.catalog.pictures[i].primary, self)
        self._loader.loaded.connect(self._on_loaded)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SCROLL_DEBOUNCE_MS)
        self._settle.timeout.connect(self.request_visible)
        self.highlight: frozenset[int] = frozenset()  # compare's cells; empty in classic
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
        self.clicked.connect(self._clicked)
        self.horizontalScrollBar().valueChanged.connect(lambda _v: self._settle.start())

    # -- rows -------------------------------------------------------------------------------------
    def rows(self) -> tuple:
        """(catalog index, derived id | None) per cell. The Band caches it, so this is a lookup."""
        return self.band.rows(len(self.catalog))

    def row_at(self, pos: int):
        rows = self.rows()
        return rows[pos] if 0 <= pos < len(rows) else None

    def row_pos(self, i: int, did=None) -> int | None:
        return self.band.row_of(i, did, len(self.catalog))

    def _span(self, i: int) -> tuple[int, int] | None:
        """First and last cell position of catalog index i: its original and everything derived from it."""
        lo = self.row_pos(i)
        if lo is None:
            return None
        nxt = self.row_pos(i + 1) if i + 1 < len(self.catalog) else None
        return lo, (nxt if nxt is not None else len(self.rows())) - 1

    def _clicked(self, ix) -> None:
        row = self.row_at(ix.row())
        if row is None:
            return
        if row[1] is None:
            self.picked.emit(row[0])
        else:
            self.picked_derived.emit(row[0], row[1])

    # -- data the delegate reads ------------------------------------------------------------------
    def _turns(self, i: int) -> int:
        try:
            return int(self.rotation_of(i)) % 4
        except Exception:
            return 0

    def edit_tags(self, i: int) -> list[tuple[str, str]]:
        try:
            got = self.edit_tag_of(i) or []
            if isinstance(got, str):
                got = [(got, "crop")]
            return [(str(t), "story" if k == "story" else "crop") for t, k in got if t]
        except Exception:  # noqa: BLE001 - a bad provider shows no tag
            return []

    def pill_colors(self, i: int) -> list[str]:
        try:
            return [str(c) for c in (self.pills_of(i) or ())]
        except Exception:  # noqa: BLE001 - a bad provider shows no dots
            return []

    def star_count(self, i: int) -> int:
        try:
            return clamp_stars(self.stars_of(i))
        except Exception:  # noqa: BLE001 - a bad provider shows no stars
            return 0

    def derived_tags(self, d) -> list[tuple[str, str]]:
        """The one tag of a derived picture: a crop is its ratio ("crop" for Free), a story is "9:16"."""
        try:
            return [(dv.tag(d, self._turns(d.source)), "story" if d.kind == dv.STORY else "crop")]
        except Exception:  # noqa: BLE001 - a bad item shows no tag
            return []

    def row_tags(self, i: int, did=None) -> list[tuple[str, str]]:
        """The tags of the cell for catalog index i (derived id `did`; None is the original)."""
        if did is None:
            return self.edit_tags(i)
        d = self.band.get(did)
        return self.derived_tags(d) if d is not None else []

    def _source_image(self, i: int):
        """The source's thumbnail image from memory (the main prefetch, else the strip's own loader), or None."""
        img = self.prefetcher.cached(i) if self.prefetcher is not None else None
        if img is None or img.isNull():  # the main prefetch does not hold it: the strip's own loader may
            img = self._loader.cached(i)
            if img is None or img.isNull():
                return None
        return img

    def _scaled(self, img) -> QPixmap:
        dpr = max(1.0, self.devicePixelRatioF())
        tw, th = round(THUMB_W * dpr), round(THUMB_H * dpr)
        big = img.scaled(tw * 3, th * 3, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.FastTransformation)
        pm = QPixmap.fromImage(big.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        pm.setDevicePixelRatio(dpr)
        return pm

    def thumb(self, i: int) -> QPixmap | None:
        turns = self._turns(i)
        got = self._memo.get((i, turns))
        if got is not None:
            return got
        img = self._source_image(i)
        if img is None:
            return None
        if turns:
            img = img.transformed(QTransform().rotate(90 * turns))
        pm = self._scaled(img)
        self._memo[(i, turns)] = pm
        while len(self._memo) > MEMO_MAX:
            del self._memo[next(iter(self._memo))]
        return pm

    def derived_thumb(self, d) -> QPixmap | None:
        """A derived picture's thumbnail, cut from its source's thumbnail in memory; None (a placeholder) until that
        is cached, and request_visible asks for the source. One entry per id: a changed item or a new turn replaces
        its cut."""
        turns = self._turns(d.source)
        key = (turns, d.kind, d.crop, d.story)
        hit = self._dmemo.get(d.id)
        if hit is not None and hit[0] == key:
            return hit[1]
        img = self._source_image(d.source)
        if img is None:
            return None
        if d.kind == dv.STORY:
            th = round(THUMB_H * max(1.0, self.devicePixelRatioF()))
            cut = story_frame(img, turns, d.story, max(1, round(th * 9 / 16)), th)
        else:
            cut = cut_crop(img, d.crop)
            if cut.isNull():
                return None
            if turns:
                cut = cut.transformed(QTransform().rotate(90 * turns))
        pm = self._scaled(cut)
        self._dmemo[d.id] = (key, pm)
        while len(self._dmemo) > MEMO_MAX:
            del self._dmemo[next(iter(self._dmemo))]
        return pm

    # -- the strip's own thumbnail requests ---------------------------------------------------------
    def visible_range(self) -> tuple[int, int]:
        """First and last cell position at least partly in view (cells are uniform, laid out left to right)."""
        n = len(self.rows())
        if n == 0:
            return 0, -1
        lo = max(0, self.horizontalScrollBar().value() // CELL_W)
        hi = (self.horizontalScrollBar().value() + self.viewport().width()) // CELL_W
        return min(lo, n - 1), min(max(lo, hi), n - 1)

    def request_visible(self) -> None:
        """Ask the loader for the visible cells plus one screen either side, nearest the middle first. A derived
        cell asks for its source's thumbnail (once per source)."""
        rows = self.rows()
        n = len(rows)
        if n == 0:
            return
        lo, hi = self.visible_range()
        span = hi - lo + 1
        a, b = max(0, lo - span), min(n - 1, hi + span)
        mid = (lo + hi) / 2
        want, seen = [], set()
        for k in sorted(range(a, b + 1), key=lambda k: abs(k - mid)):
            i = rows[k][0]
            if i in seen:
                continue
            seen.add(i)
            if self.prefetcher is None or self.prefetcher.cached(i) is None:
                want.append(i)
        dpr = max(1.0, self.devicePixelRatioF())
        self._loader.request(want, round(THUMB_W * dpr), round(THUMB_H * dpr))

    def _on_loaded(self, i: int) -> None:
        self.item_changed(i)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._settle.start()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._settle.start()

    # -- right-click: reveal the picture in the file manager ----------------------------------------
    def context_menu_for(self, pos):
        """The menu for a right-click at `pos` (viewport coordinates), or None over empty space. A derived cell
        reveals its original."""
        ix = self.indexAt(pos)
        row = self.row_at(ix.row()) if ix.isValid() else None
        return None if row is None else reveal_menu(self, self.catalog.pictures[row[0]])

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        menu = self.context_menu_for(e.pos())
        if menu is not None:
            menu.exec(e.globalPos())
            e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:  # QListView emits clicked() for any button
            e.accept()
            return
        super().mouseReleaseEvent(e)

    # -- updates from the window ------------------------------------------------------------------
    def set_current(self, i: int, did=None) -> None:
        """Select the cell of catalog index i (derived id `did`; None is the original)."""
        old = (self.current, self.current_id)
        self.current, self.current_id = i, did
        for ci, cid in (old, (i, did)):
            pos = self.row_pos(ci, cid)
            if pos is not None:
                self._model.touch(pos, pos)
        pos = self.row_pos(i, did)
        if len(self.catalog) and pos is not None:
            self.scrollTo(self._model.index(pos), QAbstractItemView.ScrollHint.PositionAtCenter)

    def band_changed(self) -> None:
        """The Band gained, lost or restored pictures: re-read the rows, keeping the scroll position."""
        if self.band.version == self._dver:
            return
        self._dver = self.band.version
        live = {d.id for d in self.band}
        for gone in [k for k in self._dmemo if k not in live]:
            del self._dmemo[gone]
        n = len(self.rows())
        if n == self._nrows:
            self.viewport().update()
            return
        self._nrows = n
        bar = self.horizontalScrollBar()
        keep = bar.value()
        self._model.beginResetModel()
        self._model.endResetModel()
        bar.setValue(keep)
        self._settle.start()

    def set_highlight(self, indices) -> None:
        """Outline these cells (compare's set); empty clears."""
        old, self.highlight = self.highlight, frozenset(int(i) for i in indices)
        for i in old | self.highlight:
            if i not in old or i not in self.highlight:
                pos = self.row_pos(i)
                if pos is not None:
                    self._model.touch(pos, pos)

    def item_changed(self, i: int, did=None) -> None:
        """A thumbnail arrived or a mark flipped: repaint index i's original and everything derived from it, or just
        the derived picture `did`."""
        if did is not None:
            pos = self.row_pos(i, did)
            if pos is not None:
                self._model.touch(pos, pos)
            return
        span = self._span(i)
        if span is not None:
            self._model.touch(*span)

    def rotation_changed(self, i: int) -> None:
        """Contract fr3: picture i's rotation changed; repaint it and its derived pictures (the memos are keyed by
        turns, so they just miss)."""
        self.item_changed(i)

    def shutdown(self, timeout_ms: int = 3000) -> None:
        """Contract fr3: stop the strip's own thumbnail workers (the window's closeEvent calls it)."""
        self._settle.stop()
        self._loader.shutdown(timeout_ms)

    def retheme(self) -> None:
        self.viewport().update()
