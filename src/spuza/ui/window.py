"""The main window: quiet bar, sidebar | viewer | inspector, filmstrip, status bar, review page, full-bleed.

Design: 02-bench, with 01-bleed as its full-screen mode. Design round 2: Direction A (the default) puts one quiet
bar on top (icon buttons + filled Copy + an overflow menu; the old QToolBars are kept hidden so their buttons keep the
state in step); Tab switches to Direction B, Lights out (only the photo, the menu bar and a floating pill), remembered
in settings. Full screen is independent of both. Ctrl+T flips the dark / light theme.
Most keys have a bar button; Mark all, Unmark all, undo and the turns ([ ]) are keys only.
The review grid and the copy sheet are built lazily through `review_factory` / `copy_sheet_factory`
so they are only imported when used (and tests can inject fakes).

Classic also has crop (X) and story fit (S): a rectangle or a 9:16 frame drawn over the big picture. Round 7: Enter
makes a DERIVED picture of it (`band`, a derived.Band shared with the filmstrip and the resume controller), placed on
the filmstrip right after its original, with its own thumbnail, tag, mark and inspector rows; the original is never
changed and can be cropped again. A derived picture is never in the Catalog: `catalog.index` stays its SOURCE's index
while one is current (`_cur_id` names it), so the prefetcher, Z, peaking and compare work on the source unchanged.
X on a derived crop (S on a derived story) edits it in place, Backspace removes it. The two overlays are built on first
use. The X edit has a ratio strip (keys 1-8, V, 0).

Advanced mode wires four more features, each behind its flag and each a no-op (key falls through, widget
hidden, nothing written) in classic: N compare (compare_view), P peaking (focus_peaking), O AF point and Z
landing on it (focus_point), and resume (resume_session; U starts fresh). Their objects are built on first use.
"""

from __future__ import annotations

import datetime
import logging
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeyEvent, QKeySequence, QPixmap, QShortcut, QPainter
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton, QSplitter,
                               QStackedWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from spuza import derived as dv
from spuza import features
from spuza import pills as pills_mod
from spuza import ratio as ratio_mod
from spuza.cards import catalog_note
from spuza.copy import human_size
from spuza.ui import actions as act
from spuza.ui import settings, theme
from spuza.ui import tokens as tk
from spuza.ui.filmstrip import Filmstrip
from spuza.ui.inspector import Inspector, derived_lines
from spuza.ui.pill import LightsOutPill
from spuza.ui.pill_tray import MANAGE_SEQ, PillTray, draw_dot, native
from spuza.ui.quiet_bar import QuietBar, tooltip_text
from spuza.ui.ratio_strip import RatioStrip
from spuza.ui.star_bar import FILLED, MAX_STARS, StarBar, clamp_stars
from spuza.ui.viewer import Viewer, info_native_size

DOT = "·"
log = logging.getLogger(__name__)

PILL_READ_MAX = 20000  # the read-back looks at this many pictures of a folder at most (each read is bounded in xmp.py)
PILL_READ_BATCH = 32  # pictures per hand-back to the UI thread
PILL_SAVE_MS = 800  # a pill change is written this long after the last one (and always on close)
PILL_JOIN_MS = 2000  # the longest closeEvent waits for the read-back and a save in flight


class _MaskSignals(QObject):
    done = Signal(int, int, object)  # generation, picture index, QImage|None


class _MaskJob(QRunnable):
    """One compare cell's peaking mask, computed off the GUI thread (QImage only, never a pixmap)."""

    def __init__(self, sig: _MaskSignals, gen: int, index: int, qimg, threshold: int):
        super().__init__()
        self.sig, self.gen, self.index, self.qimg, self.threshold = sig, gen, index, qimg, threshold
        self.setAutoDelete(True)

    def run(self) -> None:
        from spuza import peaking

        try:
            out = peaking.mask_image(self.qimg, self.threshold)
        except Exception:  # noqa: BLE001 - a failed mask leaves the plain cell
            out = None
        try:
            self.sig.done.emit(self.gen, self.index, out)
        except RuntimeError:  # the window was destroyed meanwhile
            pass


class _PillSignals(QObject):
    batch = Signal(int, list)  # generation, [(picture index, [PillTag] | None, rating 1..5 | None)]: a pills bag or a rating
    finished = Signal(int, int)  # generation, pictures looked at


class _PillReadJob(QRunnable):
    """Reads every picture's pills and star rating from its XMP, in one read, off the GUI thread (xmp.read_picture_meta), and warms the index's
    per-file keys so the UI thread never has to open a file to draw a dot. Stops at once when `cancel` is set (another
    folder loaded, or the window closes) and after PILL_READ_MAX pictures."""

    def __init__(self, sig: _PillSignals, gen: int, pictures, index, cancel: threading.Event):
        super().__init__()
        self.sig, self.gen, self.pictures, self.index, self.cancel = sig, gen, pictures, index, cancel
        self.setAutoDelete(True)

    def _emit(self, signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:  # the window was destroyed meanwhile
            pass

    def run(self) -> None:
        from spuza import xmp

        batch, seen = [], 0
        for k, pic in enumerate(self.pictures[:PILL_READ_MAX]):
            if self.cancel.is_set():
                return
            seen = k + 1
            try:
                self.index.key(pic)
            except Exception:  # noqa: BLE001 - a key we cannot make is made again on use
                pass
            try:
                tags, rating = xmp.read_picture_meta(pic)
            except Exception:  # noqa: BLE001 - an unreadable file has no pills and no rating
                tags, rating = None, None
            if tags is not None or rating is not None:
                batch.append((k, tags, rating))
            if len(batch) >= PILL_READ_BATCH:
                self._emit(self.sig.batch, self.gen, batch)
                batch = []
        if batch and not self.cancel.is_set():
            self._emit(self.sig.batch, self.gen, batch)
        if not self.cancel.is_set():
            self._emit(self.sig.finished, self.gen, seen)


class _PillSaveJob(QRunnable):
    """Writes the pill index (it takes its own lock, so the UI may keep changing it). Never raises."""

    def __init__(self, index):
        super().__init__()
        self.index = index
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            self.index.save()
        except Exception as e:  # noqa: BLE001 - a failed save must never reach the UI
            log.warning("could not save the pill index: %s", e)


def default_copy_dest() -> Path:
    """Where Copy… starts: a folder on the drive, never the card being culled (tests patch Path.home)."""
    return Path.home() / "Pictures" / "Spuza"


def month_day(d) -> str:
    """MM_DD of a date, zero padded (22 October is 10_22): the folder name a master-folder copy goes into."""
    return f"{d.month:02d}_{d.day:02d}"


_NOT_IN_REVIEW = object()
GRID_KEYS = frozenset({Qt.Key_Space, Qt.Key_M, Qt.Key_Right, Qt.Key_Left, Qt.Key_Up, Qt.Key_Down,
                       Qt.Key_Return, Qt.Key_Enter, Qt.Key_Z, Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus, Qt.Key_Escape,
                       Qt.Key_J, Qt.Key_K})  # J / K step a set on the review's zoom page; the grid ignores them
# (name, label, key cap shown on the button, checkable). Mark all and the turns have no buttons (round 4: they
# did not fit the one classic row at 1280 px); Ctrl+A, Ctrl+Shift+A, Ctrl+Z, [ and ] are window shortcuts only.
PREFS_CAP = "⌘," if sys.platform == "darwin" else "Ctrl+,"
BUTTONS = [
    ("back", "Back", "←/J", False),
    ("next", "Next", "→/K", False),
    ("mark", "Mark", "Space/M", True),
    ("advance", "Move on mark", "A", True),
    ("zoom", "Zoom 100%", "Z", True),
    ("fullscreen", "Full screen", "F", True),
    ("review", "Review", "R", True),
    ("copy", "Copy…", "C", False),
]
# advanced-only controls (each shown only while its flag is on): (name, label, key cap, flag)
NEXT_BUTTONS = [
    ("compare", "Compare", "N", "compare_view"),
    ("peaking", "Peaking", "P", "focus_peaking"),
    ("afpoint", "AF point", "O", "focus_point"),
]
# the buttons the quiet bar shows icon-only, in order (Copy is its filled primary button)
STAR_ACTIONS = tuple(f"stars_{n}" for n in range(1, MAX_STARS + 1)) + ("stars_clear",)
BAR_ICONS = {"back": "arrow-left", "next": "arrow-right", "mark": "circle-check", "zoom": "zoom-in",
             "review": "layout-grid"}
# the menu actions the bar's overflow (the ellipsis button) holds, in order; None is a separator before the advanced ones
OVERFLOW = ("advance", "fullscreen", "prefs", "lights_out", None, "compare", "peaking", "afpoint")
COMPARE_KEYS = frozenset({Qt.Key_Left, Qt.Key_Right, Qt.Key_J, Qt.Key_K, Qt.Key_Space, Qt.Key_M, Qt.Key_Z,
                          Qt.Key_N, Qt.Key_Escape})
PEAKING_WARM_MS = 3000  # a saved-on peaking at start is built when the prefetch goes idle, or after this at the latest
MASK_WAIT_MS = 3000  # the longest closeEvent waits for compare's mask workers


class KeyButton(QToolButton):
    def __init__(self, name: str, label: str, cap: str, checkable: bool, parent=None):
        super().__init__(parent)
        self.name, self.label, self.cap = name, label, cap
        self.setText(f"{label}   {cap}")
        self.setCheckable(checkable)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip_text(label, cap))  # one line, "Name  Key": the quiet bar shows these icon-only
        self.setAccessibleName(label)
        self.setObjectName(f"btn_{name}")

    def set_label(self, label: str) -> None:
        self.label = label
        self.setText(f"{label}   {self.cap}")
        self.setToolTip(tooltip_text(label, self.cap))
        self.setAccessibleName(label)


class _PillRow(QWidget):
    """One pill in the sidebar: dot, name, count. Clickable (not a button: it has no key of its own); the pill's
    number key toggles the pill on a picture, a click here shows the pictures that carry it."""

    clicked = Signal()

    def __init__(self, name: str, color: str, parent=None):
        super().__init__(parent)
        self.name, self.color = name, color
        self.setObjectName("pillRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.dot = QLabel()
        self.dot.setFixedSize(14, 14)
        self.label = QLabel(name)
        self.label.setToolTip(f"Show the pictures with {name}")
        self.count = QLabel("0")
        row.addSpacing(6)
        row.addWidget(self.dot)
        row.addWidget(self.label, 1)
        row.addWidget(self.count)
        self.setToolTip(f"Show the pictures with {name}")
        self.paint_dot()

    def paint_dot(self) -> None:
        pm = QPixmap(14, 14)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        draw_dot(painter, 7, 7, 5, self.color, theme.current().dark)
        painter.end()
        self.dot.setPixmap(pm)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class _StarChip(QLabel):
    """One "N★" chip of the sidebar's threshold row. A click asks for N, or for none when it is the lit one."""

    clicked = Signal()

    def __init__(self, n: int, parent=None):
        super().__init__(f"{n}{FILLED}", parent)
        self.n = n
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(f"Only pictures with at least {n} star{'s' if n > 1 else ''}")
        self.setAccessibleName(f"At least {n} stars")

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


def _lead(text: str) -> int:
    """The number a count label starts with ("2 of 5" -> 2, "7" -> 7); 0 when it has none."""
    head = text.split(" ", 1)[0]
    return int(head) if head.isdigit() else 0


class Sidebar(QWidget):
    """Source root and counts; 'All' is always above 'Marked', then one row per pill, then the "at least N stars"
    threshold chips. While a threshold is set every count reads "n of m"."""

    show_all = Signal()
    show_marked = Signal()
    show_pill = Signal(str)  # a pill row was clicked: its name
    min_stars_changed = Signal(int)  # the threshold chips: 1..5, or 0 when the lit one was clicked off

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(170)
        self.setMaximumWidth(260)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.head = QLabel("SOURCE")
        self.root_label = QLabel()
        self.root_label.setWordWrap(True)
        lay.addWidget(self.head)
        lay.addWidget(self.root_label)
        lay.addSpacing(10)
        self._rows: list[tuple[str, QPushButton, QLabel]] = []
        for name, sig in (("All", self.show_all), ("Marked", self.show_marked)):
            row = QHBoxLayout()
            btn = QPushButton(name)
            btn.setFlat(True)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setToolTip("All  Esc" if name == "All" else "Marked  R")  # names its key (design policy)
            btn.clicked.connect(lambda _=False, s=sig: s.emit())
            cnt = QLabel("0")
            row.addWidget(btn, 1)
            row.addWidget(cnt)
            lay.addLayout(row)
            self._rows.append((name, btn, cnt))
        self._lay = lay
        self._pill_rows: list[_PillRow] = []
        self._pill_sig: list[tuple[str, str]] = []
        self._open: tuple[str, str | None] | None = None  # ("marked", None) or ("pill", name): the set shown in review
        self._min_stars = 0
        self.stars_head = QLabel("STARS")
        self.stars_hint = QLabel("at least")
        self._chips = [_StarChip(n) for n in range(1, MAX_STARS + 1)]
        chips = QHBoxLayout()
        chips.setSpacing(2)
        for chip in self._chips:
            chip.clicked.connect(lambda n=chip.n: self._chip_clicked(n))
            chips.addWidget(chip)
        lay.addSpacing(10)
        lay.addWidget(self.stars_head)
        lay.addWidget(self.stars_hint)
        lay.addLayout(chips)
        lay.addStretch(1)
        self.set_root(root)

    def set_root(self, root: Path) -> None:
        self._root = str(root)
        self.root_label.setText(self._root)
        self.root_label.setToolTip(self._root)

    def root_text(self) -> str:
        return self._root

    def set_counts(self, total: int, marked: int, shown=None) -> None:
        """`shown`: (All, Marked) counts that pass the star threshold; given, the labels read "n of m"."""
        for k, ((name, _b, cnt), n) in enumerate(zip(self._rows, (total, marked))):
            cnt.setText(str(n) if shown is None else f"{shown[k]} of {n}")

    def min_stars(self) -> int:
        return self._min_stars

    def set_min_stars(self, n: int) -> None:
        """Light the chip of threshold `n` (0: none). Silent: only a click emits min_stars_changed."""
        self._min_stars = max(0, min(MAX_STARS, int(n)))
        self._style_chips()

    def star_chip(self, n: int) -> QLabel:
        return self._chips[n - 1]

    def _chip_clicked(self, n: int) -> None:
        self.set_min_stars(0 if n == self._min_stars else n)
        self.min_stars_changed.emit(self._min_stars)

    def _style_chips(self) -> None:
        c = theme.current()
        self.stars_head.setStyleSheet(f"color: {c.text_dim}; font-size: {tk.TYPE['caption']}px; "
                                      f"font-weight: {tk.TYPE['weight_strong']};")
        self.stars_hint.setStyleSheet(f"color: {c.text_dim};")
        for chip in self._chips:
            on = chip.n == self._min_stars
            chip.setProperty("lit", on)
            chip.setStyleSheet(f"background: {c.focus if on else c.raised}; color: {c.on_focus if on else c.text};"
                               " border-radius: 4px; padding: 3px 0px;")

    def set_pills(self, rows) -> None:
        """rows: (name, colour name, picture count[, count that passes the star threshold]) in the book's order.
        Rebuilt only when the names or colours changed; a count alone just updates its label ("n of m" with the
        fourth field)."""
        shown = [int(r[3]) if len(r) > 3 else None for r in rows]
        rows = [(str(r[0]), str(r[1]), int(r[2])) for r in rows]
        sig = [(n, c) for n, c, _k in rows]
        if sig != self._pill_sig:
            for r in self._pill_rows:
                self._lay.removeWidget(r)
                r.hide()
                r.deleteLater()
            self._pill_rows = []
            at = self._lay.indexOf(self.stars_head) - 1  # just above the stars section's spacing
            for n, c, _k in rows:
                r = _PillRow(n, c)
                r.clicked.connect(lambda _n=n: self.show_pill.emit(_n))
                self._lay.insertWidget(at, r)
                at += 1
                self._pill_rows.append(r)
            self._pill_sig = sig
            self.retheme()
        for r, (_n, _c, k), got in zip(self._pill_rows, rows, shown):
            r.count.setText(str(k) if got is None else f"{got} of {k}")
        self._style_open()

    def set_open(self, kind: str | None, name: str | None = None) -> None:
        """Highlight the row of the set review shows (kind "marked" or "pill"), none with None."""
        self._open = None if kind is None else (kind, name)
        self._style_open()

    def open_row(self) -> str | None:
        """The name of the highlighted row ("Marked" or a pill's name), None when no set is open."""
        if self._open is None:
            return None
        return "Marked" if self._open[0] == "marked" else self._open[1]

    def _is_open(self, row: _PillRow) -> bool:
        return (self._open is not None and self._open[0] == "pill"
                and str(self._open[1]).casefold() == row.name.casefold())

    def _style_open(self) -> None:
        c = theme.current()
        for name, btn, _cnt in self._rows:
            on = name == "Marked" and self._open is not None and self._open[0] == "marked"
            btn.setProperty("openSet", on)
            btn.setStyleSheet(f"text-align: left; padding: 4px 6px; color: {c.text}; border: 0;"
                              + (f" background: {c.raised}; font-weight: bold;" if on else ""))
        for r in self._pill_rows:
            on = self._is_open(r)
            r.setProperty("openSet", on)
            r.setStyleSheet(f"#pillRow {{ background: {c.raised if on else 'transparent'}; }}")
            f = r.label.font()
            f.setBold(on)
            r.label.setFont(f)

    def pill_row_names(self) -> list[str]:
        return [r.name for r in self._pill_rows]

    def pill_counts(self) -> dict[str, int]:
        return {r.name: _lead(r.count.text()) for r in self._pill_rows}

    def pill_row(self, name: str) -> _PillRow | None:
        for r in self._pill_rows:
            if r.name.casefold() == str(name).casefold():
                return r
        return None

    def row_names(self) -> list[str]:
        return [n for n, _b, _c in self._rows]

    def counts(self) -> dict[str, int]:
        return {n: _lead(c.text()) for n, _b, c in self._rows}

    def count_texts(self) -> dict[str, str]:
        """The labels as shown: "7", or "2 of 7" while a star threshold is set (the pill rows too)."""
        out = {n: c.text() for n, _b, c in self._rows}
        out.update({r.name: r.count.text() for r in self._pill_rows})
        return out

    def retheme(self) -> None:
        c = theme.current()
        self.head.setStyleSheet(f"color: {c.text_dim}; font-size: {tk.TYPE['caption']}px; "
                                f"font-weight: {tk.TYPE['weight_strong']};")
        for _n, _btn, cnt in self._rows:
            cnt.setStyleSheet(f"color: {c.text_dim};")
        for r in self._pill_rows:
            r.count.setStyleSheet(f"color: {c.text_dim};")
            r.label.setStyleSheet(f"color: {c.text};")
            r.paint_dot()
        self._style_chips()
        self._style_open()


class MainWindow(QMainWindow):
    peaking_settle_ms = None  # None: the overlay's SETTLE_MS; tests set 0 so no assertion depends on a delay
    step_timed = Signal(int, float)  # picture index, ms from key handled to its pixmap set

    def __init__(self, catalog, prefetcher, root, clock=time.monotonic, parent=None, prefs=None, resume=True,
                 card_note="", card_mount: Path | None = None):
        super().__init__(parent)
        self.card_mount = Path(card_mount) if card_mount else None  # the detected camera card to eject, else None
        self._resume_allowed = bool(resume)  # False for --bench: a scripted run neither reads nor writes sessions
        self.catalog, self.prefetcher, self.root = catalog, prefetcher, Path(root)
        self.prefs = prefs if prefs is not None else settings.open_settings()
        self.advance_on_mark = settings.read_bool(self.prefs, settings.ADVANCE_ON_MARK, False)
        self.clock = clock  # kept for callers that pass it; the status strip no longer uses it
        self.review_factory = None
        self.copy_sheet_factory = None
        self.today = datetime.date.today  # injectable: the master folder's MM_DD comes from it
        self.prefs_sheet_factory = None
        self.review = None
        self.compare = None  # advanced: CompareView, built on first N
        self.peaking = None  # advanced: PeakingOverlay, built when the flag is on
        self.marker = None  # advanced: FocusMarker
        self.resume = None  # advanced: ResumeController
        self._compare_page = None
        self._compare_strip = None
        self._compare_origin = "single"
        self._review_slot = None  # holds stack page 1 for the review until it is built, if compare comes first
        self._cmp_pool = None
        self._cmp_sig = None
        self._cmp_gen = 0
        self._cmp_masked: set[int] = set()
        self._banner = None
        self._peaking_wait = False
        self._pruned = False
        self._prune_thread = None
        self._sheet = None
        self._prefs_sheet = None
        self._prefs_seed = False
        self.last_message = ""
        self.rotations: dict[int, int] = {}  # friend round 3: catalog index -> clockwise quarter turns 1..3 (0 omitted)
        self.band = dv.Band()  # friend round 7: every derived picture (crop / story fit), shared by reference
        self._cur_id: int | None = None  # the derived picture on screen (of catalog.index), None: the original itself
        self._edit_target: int | None = None  # the derived id the open edit replaces (None: it adds a new one)
        self._edit_view = False  # an edit is open on a derived row: the viewer shows the source's own pixels
        self._crop_ratio = ratio_mod.FREE  # the open crop edit's ratio
        self._crop_native = (1, 1)  # the open crop edit's un-turned native size
        self._rect_auto = False  # the open crop's rectangle is still the untouched largest-centred one
        self._crop_overlay = None  # CropOverlay, built on first X (or the first picture that has a crop)
        self._story_editor = None  # StoryEditor, built on first S
        self._story_native = (1, 1)  # the open story edit's un-turned native size
        self._edit: str | None = None  # None | "crop" | "story": the mode the viewer is in
        self._markall_undo: frozenset[int] | None = None  # the marks before the last Mark all / Unmark all
        self._markall_after: frozenset[int] = frozenset()  # the marks right after it (to count later changes)
        self._markall_undo_d: frozenset[int] = frozenset()  # ... the marked derived ids before it
        self._markall_after_d: frozenset[int] = frozenset()  # ... and right after it
        self._markall_dids: frozenset[int] = frozenset()  # the derived ids that existed then (later ones are not undone)
        self.is_bleed = False
        self._forwarding = False
        self._was_maximized = False
        self._t_key: float | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # can hold focus, so close_review can restore it
        self.setWindowTitle(f"Spuza — {self.root}")
        self.resize(1280, 800)

        # pills: the model (the user's book, and which pills each picture carries), kept in app data (SPUZA_PILLS_DIR)
        self.pill_book = pills_mod.PillBook.load()
        self.pill_index = pills_mod.PillIndex.load()
        self._pill_mgr = None
        self._pill_dirty = False
        self._pill_ready = False  # the read-back has finished: the strip and the review may draw every picture's dots
        self._pill_touched: set = set()  # pictures changed by hand before then (their dots show at once)
        self._pill_counts: dict[str, int] | None = None
        self._star_counts: tuple | None = None  # (pictures passing the threshold, {pill name casefolded: n}); None: stale
        self._min_stars = 0  # the sidebar's "at least N stars" threshold (0: off)
        self._file_stars: dict = {}  # Picture.primary -> the 1..5 xmp:Rating the file carried when the folder loaded
        self._stars_touched: set = set()  # pictures whose stars were set by hand (their stars show before the read ends)
        self._pill_snapshot: list[str] = [p.name for p in self.pill_book.pills()]
        self._pill_gen = 0
        self._pill_cancel = threading.Event()
        self._pill_sig = _PillSignals()
        self._pill_sig.batch.connect(self._on_pill_batch)
        self._pill_sig.finished.connect(self._on_pill_finished)
        self._pill_read_pool = QThreadPool()
        self._pill_read_pool.setMaxThreadCount(1)
        self._pill_save_pool = QThreadPool()
        self._pill_save_pool.setMaxThreadCount(1)
        self._pill_save_timer = QTimer(self)
        self._pill_save_timer.setSingleShot(True)
        self._pill_save_timer.timeout.connect(self._flush_pill_save)

        self.viewer = Viewer()
        self.sidebar = Sidebar(self.root)
        self.inspector = Inspector()
        self.filmstrip = Filmstrip(catalog, prefetcher, band=self.band)
        self.viewer.rotation_of = self.filmstrip.rotation_of = self._rotation_of
        self.filmstrip.pills_of = self._pill_colors_of
        self.filmstrip.stars_of = self._stars_of

        self.toolbar = QToolBar("Main", self)
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.addToolBar(self.toolbar)
        self.buttons: dict[str, KeyButton] = {}
        # The advanced-only buttons live on a second row: on one row the three would push Preferences and Copy
        # into the overflow chevron at 1280 px (measured). The row is hidden, never removed, in classic.
        self.addToolBarBreak()
        self.toolbar2 = QToolBar("Advanced", self)
        self.toolbar2.setMovable(False)
        self.toolbar2.setFloatable(False)
        self.addToolBar(self.toolbar2)
        self.next_buttons: dict[str, KeyButton] = {}
        self._next_actions: dict[str, object] = {}
        for nname, nlabel, ncap, _flag in NEXT_BUTTONS:
            nb = KeyButton(nname, nlabel, ncap, True)
            self.next_buttons[nname] = nb
            self._next_actions[nname] = self.toolbar2.addWidget(nb)
        self.toolbar2.setVisible(False)
        # Direction A: the quiet bar holds Back, Next, Mark, Zoom, Review (icon-only) and a filled Copy; Move on mark,
        # Full screen, Preferences and the advanced three live in its overflow menu. The toolbars are never shown
        # now; they keep those buttons (and their checked state, which the menu and tests read) in step.
        # Preferences is not in `buttons` (those are the key-driven picture controls, kept in step by review mode).
        self.quiet_bar = QuietBar()
        self.quiet_bar.set_title(self.root.name or str(self.root), len(catalog))
        self.pill_tray = PillTray(lambda: self.pill_book.pills(), self._current_pill_names)
        self.pill_tray.toggled.connect(self._tray_toggled)
        self.pill_tray.manage.connect(lambda: self.open_pill_manager())
        self.pill_tray.more.connect(self._tray_more)
        self.quiet_bar.set_tray(self.pill_tray)
        self.star_bar = StarBar()  # five clickable stars after the tray; it gives way on a narrow window
        self.star_bar.rated.connect(self._star_bar_rated)
        self.quiet_bar.set_star_bar(self.star_bar)
        self.prefs_button = KeyButton("prefs", "Preferences…", PREFS_CAP, False)
        for name, label, cap, checkable in BUTTONS:
            b = KeyButton(name, label, cap, checkable)
            self.buttons[name] = b
            if name in BAR_ICONS:
                self.quiet_bar.add_button(b, BAR_ICONS[name])
            elif name == "copy":
                self.quiet_bar.set_copy_button(b)
            else:
                self.toolbar.addWidget(b)
        self.toolbar.addWidget(self.prefs_button)
        self.toolbar.setVisible(False)
        b = self.buttons
        # the toolbar buttons and the menu actions (ui/actions.py) run the same act_* methods
        b["back"].clicked.connect(lambda: self.act_back())
        b["next"].clicked.connect(lambda: self.act_next())
        b["mark"].clicked.connect(lambda: self.act_mark())
        b["advance"].clicked.connect(lambda: self.act_advance())
        b["zoom"].clicked.connect(lambda: self.act_zoom())
        b["fullscreen"].clicked.connect(lambda: self.act_fullscreen())
        b["review"].clicked.connect(lambda: self.act_review())
        b["copy"].clicked.connect(lambda: self.act_copy())
        self.prefs_button.clicked.connect(lambda: self.act_prefs())
        nb = self.next_buttons
        nb["compare"].clicked.connect(lambda: self.act_compare())
        nb["peaking"].clicked.connect(lambda: self.act_peaking())
        nb["afpoint"].clicked.connect(lambda: self.act_afpoint())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.sidebar)
        self.ratio_strip = RatioStrip()  # round 6: above the picture, only while the X crop edit is open
        self.ratio_strip.setVisible(False)
        self.ratio_strip.picked.connect(self._pick_ratio)
        self.ratio_strip.turn.connect(self._turn_ratio)
        self.ratio_strip.reset.connect(self._reset_ratio)
        stage = QWidget()
        stage_lay = QVBoxLayout(stage)
        stage_lay.setContentsMargins(0, 0, 0, 0)
        stage_lay.setSpacing(0)
        stage_lay.addWidget(self.ratio_strip)
        stage_lay.addWidget(self.viewer, 1)
        split.addWidget(stage)
        split.addWidget(self.inspector)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        split.setSizes([200, 800, 250])
        page0 = QWidget()
        lay = QVBoxLayout(page0)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(split, 1)
        lay.addWidget(self.filmstrip)
        self.stack = QStackedWidget()
        self.stack.addWidget(page0)
        central = QWidget()  # the quiet bar over the stack (the bar hides in Lights out and full screen)
        central_lay = QVBoxLayout(central)
        central_lay.setContentsMargins(0, 0, 0, 0)
        central_lay.setSpacing(0)
        central_lay.addWidget(self.quiet_bar)
        central_lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        # Direction B: the pill floats over the photo (a child of the stack, so over the single view only)
        self.lights_out = settings.read_bool(self.prefs, settings.LIGHTS_OUT, False)
        self.pill = LightsOutPill(self.stack)
        self.pill.copy_clicked.connect(lambda: self.act_copy())
        self.pill.help_clicked.connect(lambda: self.act_help())
        self.pill.help_button().setToolTip(tooltip_text("Keyboard shortcuts", "?"))
        self.pill.copy_button().setToolTip(tooltip_text("Copy…", "C"))
        self._toast = QLabel(self.stack)  # Lights out has no status bar: say() shows its message here
        self._toast.setObjectName("lightsOutNote")
        self._toast.setWordWrap(True)
        self._toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)

        sb = self.statusBar()
        self.lbl_pos, self.lbl_marked = QLabel(), QLabel()
        sb.addWidget(self.lbl_pos)
        sb.addWidget(self.lbl_marked)
        # several cards mounted / files the scan set aside: one quiet label, empty (and hidden) when nothing to say
        self.lbl_cards = QLabel(" · ".join(t for t in (card_note, catalog_note(catalog)) if t))
        self.lbl_cards.setVisible(bool(self.lbl_cards.text()))
        sb.addWidget(self.lbl_cards)
        self.advanced_tag = QLabel("ADVANCED")
        self.advanced_tag.setStyleSheet("font-weight: bold; padding: 0 8px;")
        sb.addPermanentWidget(self.advanced_tag)
        self._show_mode()

        prefetcher.ready.connect(self._on_ready)
        prefetcher.failed.connect(self._on_failed)
        sized = getattr(prefetcher, "sized", None)  # a raw's frame size arrives after its image
        if sized is not None:
            sized.connect(self._on_sized)
        self.viewer.shown.connect(self._on_shown)
        self.viewer.zoom_changed.connect(lambda z: self.buttons["zoom"].setChecked(z))
        self.filmstrip.picked.connect(self.go_to)
        self.filmstrip.picked_derived.connect(self.go_to)
        self.sidebar.show_all.connect(self.close_review)
        self.sidebar.show_marked.connect(lambda: self.open_review())
        self.sidebar.show_pill.connect(self.open_pill_set)
        self.sidebar.min_stars_changed.connect(self._set_min_stars)
        QShortcut(QKeySequence(QKeySequence.StandardKey.FullScreen), self,
                  activated=lambda: self.set_bleed(not self.is_bleed))
        advance = QShortcut(QKeySequence("A"), self, context=Qt.ShortcutContext.WindowShortcut,
                            activated=lambda: self.set_advance_on_mark(not self.advance_on_mark))
        advance.setAutoRepeat(False)  # a held A must not flip the preference back and forth
        # Ctrl+, (Cmd+, on macOS: Qt maps Ctrl to Cmd). Not StandardKey.Preferences: it is empty on Windows.
        # keyPressEvent hands Ctrl-modified keys to super, so this shortcut is the only path.
        prefs_sc = QShortcut(QKeySequence("Ctrl+,"), self, context=Qt.ShortcutContext.WindowShortcut,
                             activated=lambda: self.open_prefs())
        prefs_sc.setAutoRepeat(False)
        # Mark all / Unmark all / Undo and the two rotations are window shortcuts, so they work with the review grid
        # focused too (a window shortcut is matched before the list view's own select-all key handling).
        for seq, fn in ((QKeySequence("Ctrl+A"), lambda: self.mark_all(True)),
                        (QKeySequence("Ctrl+Shift+A"), lambda: self.mark_all(False)),
                        (QKeySequence("Ctrl+Z"), lambda: self.undo_mark_all()),
                        (QKeySequence("["), lambda: self.rotate(-1)), (QKeySequence("]"), lambda: self.rotate(1))):
            sc = QShortcut(seq, self, context=Qt.ShortcutContext.WindowShortcut, activated=fn)
            sc.setAutoRepeat(False)  # a held key must not mark, undo or turn repeatedly
        pills_sc = QShortcut(QKeySequence("Ctrl+Shift+P"), self, context=Qt.ShortcutContext.WindowShortcut,
                             activated=lambda: None if self._overlay_open() else self.open_pill_manager())
        pills_sc.setAutoRepeat(False)  # a held key must not stack managers
        for n in range(0, MAX_STARS + 1):  # Ctrl/Cmd+1..5 set stars, Ctrl/Cmd+0 clears (plain digits stay pills)
            star_sc = QShortcut(QKeySequence(f"Ctrl+{n}"), self, context=Qt.ShortcutContext.WindowShortcut,
                                activated=lambda n=n: self._stars_key(n))
            star_sc.setAutoRepeat(False)  # a held key must not toast and save again and again
        quit_sc = QShortcut(QKeySequence("Ctrl+Q"), self, context=Qt.ShortcutContext.WindowShortcut,
                            activated=lambda: self.act_quit())  # StandardKey.Quit is empty on Windows
        quit_sc.setAutoRepeat(False)
        # Tab is Qt's focus key, and whichever child holds focus (the window, the review grid) would take it for
        # focus navigation; a window shortcut is matched before the key reaches any widget, and not at all while a
        # dialog (the copy or prefs sheet, a line edit) is the active window, so Tab there still moves focus.
        tab_sc = QShortcut(QKeySequence("Tab"), self, context=Qt.ShortcutContext.WindowShortcut,
                           activated=lambda: None if self._overlay_open() else self.act_lights_out())
        tab_sc.setAutoRepeat(False)  # a held Tab must not flip the layout back and forth
        theme_sc = QShortcut(QKeySequence("Ctrl+T"), self, context=Qt.ShortcutContext.WindowShortcut,
                             activated=lambda: self.act_dark_theme())
        theme_sc.setAutoRepeat(False)
        self._shortcuts = None  # the `?` overlay, built on each open
        self._build_menus()
        self.quiet_bar.overflow_menu().aboutToShow.connect(self.refresh_actions)
        self._build_overflow()

        self.retheme()
        self.buttons["advance"].setChecked(self.advance_on_mark)
        self.viewer.zoom_full_sensor = settings.read_zoom_full_sensor(self.prefs)
        self.viewer.pixmap_changed.connect(self._marker_follows_frame)
        self.viewer.zoom_target = self._zoom_target  # None unless advanced + focus_point: Z centres as shipped
        if features.enabled("resume_session"):
            # restore BEFORE the first prefetch so the first paint is the restored place, and no mark can be made
            # (and saved over the old session) before it ran
            ctl = self._ensure_resume()
            self._banner = ctl.restore() if ctl is not None else None
            self._band_changed()  # a restored session brings its derived pictures
        prefetcher.update(catalog.index)
        self.start_pill_readback()
        self._enter_next_features(defer=True)  # after update(): importing numpy for peaking must not delay the first decodes
        self._render_current()

    # -- navigation -------------------------------------------------------------------------------
    def _rows(self) -> tuple:
        """The filmstrip's rows: (catalog index, derived id | None), each original followed by its derived pictures."""
        return self.band.rows(len(self.catalog))

    def _row_pos(self):
        return self.band.row_of(self.catalog.index, self._cur_id, len(self.catalog))

    def _current_derived(self):
        """The derived picture on screen (a Derived of catalog.index), or None when the original is."""
        if self._cur_id is None:
            return None
        d = self.band.get(self._cur_id)
        if d is None or d.source != self.catalog.index:
            self._cur_id = None
            return None
        return d

    def step(self, delta: int) -> None:
        if not self.band:  # no derived picture anywhere: the plain walk over the catalog
            self._cur_id = None
            if (self.catalog.next() if delta > 0 else self.catalog.back()):
                self._moved()
            return
        pos = self._row_pos()
        if pos is None:
            self._cur_id = None
            pos = self.band.row_of(self.catalog.index, None, len(self.catalog))
        rows = self._rows()
        if pos is None or not 0 <= pos + delta < len(rows):
            return
        src, did = rows[pos + delta]
        self.catalog.go(src)
        self._cur_id = did
        self._moved()

    def go_to(self, i: int, did=None) -> None:
        """Show catalog index i, or the derived picture `did` of it."""
        if self._compare_shown():
            return  # the filmstrip is behind compare; a stray pick must not move the viewer under it
        if self.stack.currentIndex() == 1:
            if did is None:
                self._select_in_review(i)  # review stays up; the viewer's picture does not move
            return  # the review lists originals only
        if did is not None:
            d = self.band.get(did)
            if d is None or d.source != i:
                return
        if len(self.catalog) and (i != self.catalog.index or did != self._cur_id):
            self.catalog.go(i)
            self._cur_id = did
            self._moved()
            if self.stack.currentIndex() == 1:
                self._focus_review()

    def _moved(self) -> None:
        self._end_edit(restore=False)  # a crop or story edit in progress is cancelled by moving on
        self._t_key = time.perf_counter()
        self.prefetcher.update(self.catalog.index)
        self._render_current()

    def toggle_mark(self) -> None:
        if not len(self.catalog):
            return
        i = self.catalog.index
        d = self._current_derived()
        if d is not None:
            self.band.set_marked(d.id, not d.marked)
            self.filmstrip.item_changed(i, d.id)
        else:
            self.catalog.toggle_mark(i)
            self.filmstrip.item_changed(i)
        self.refresh_marks()
        if self.advance_on_mark and self.stack.currentIndex() == 0:
            self.step(1)  # "move on mark": mark or unmark, the pick is decided; show the next (last: stays)

    def set_advance_on_mark(self, on: bool, announce: bool = True) -> None:
        """Toggle and persist "move on mark"; the toolbar button mirrors it."""
        on = bool(on)
        self.advance_on_mark = on
        self.prefs.setValue(settings.ADVANCE_ON_MARK, on)
        self.prefs.sync()
        self.buttons["advance"].setChecked(on)
        if self.compare is not None:
            self.compare.set_advance_on_mark(on)
        if announce:
            self.say("Move on mark: on (marking shows the next picture)" if on else "Move on mark: off")

    # -- mark all / unmark all (one level of undo) -------------------------------------------------
    def _all_marked(self) -> bool:
        return len(self.catalog) > 0 and self._marked_total() == len(self.catalog) + len(self.band)

    def _marks_set(self) -> frozenset[int]:
        c = self.catalog
        return frozenset(i for i in range(len(c)) if c.is_marked(i))

    def _dmarks_set(self) -> frozenset[int]:
        """The ids of the marked derived pictures."""
        return frozenset(d.id for d in self.band if d.marked)

    def _marked_total(self) -> int:
        """Marked originals plus marked derived pictures."""
        return self.catalog.marked_count + sum(1 for d in self.band if d.marked)

    def _marks_changed_everywhere(self) -> None:
        """The same refreshes toggle_mark does, plus the review grid (which lists only marked pictures) and compare.
        The resume controller polls the marks on its own tick, as it does after a single mark."""
        self.filmstrip.viewport().update()
        if self.review is not None and hasattr(self.review, "refresh"):
            self.review.refresh()
        if self._compare_shown():
            self._sync_compare()
        self.refresh_marks()

    def mark_all(self, on: bool) -> None:
        """Mark (or unmark) every picture, derived ones too, never asking first. The marks from before are kept for
        ONE Ctrl+Z; the snapshot lives until the next Mark all / Unmark all or U (start fresh) replaces it, or the
        window closes."""
        c = self.catalog
        n = len(c) + len(self.band)
        if not len(c):
            self.say("No pictures to mark")
            return
        before, dbefore = self._marks_set(), self._dmarks_set()
        have = len(before) + len(dbefore)
        changed = n - have if on else have
        if changed == 0:
            self.say("Everything is already marked" if on else "Nothing is marked")
            return
        self._markall_undo, self._markall_undo_d = before, dbefore
        self._markall_dids = frozenset(d.id for d in self.band)
        for i in range(len(c)):
            c.set_mark(i, on)
        for d in self.band:
            self.band.set_marked(d.id, on)
        self._markall_after, self._markall_after_d = self._marks_set(), self._dmarks_set()
        self._marks_changed_everywhere()
        if on:
            self.say(f"Marked {changed} (Ctrl+Z undoes)" if changed == n
                     else f"Marked {changed} more, {n} in all (Ctrl+Z undoes)")
        else:
            self.say(f"Unmarked {changed} (Ctrl+Z undoes)")

    def undo_mark_all(self) -> None:
        prev, self._markall_undo = self._markall_undo, None
        if prev is None:
            self.say("Nothing to undo")
            return
        dprev, dids = self._markall_undo_d, self._markall_dids
        c = self.catalog
        later = (len(self._marks_set() ^ self._markall_after)  # pictures marked or unmarked by hand since the action
                 + len((self._dmarks_set() ^ self._markall_after_d) & dids))
        for i in range(len(c)):
            c.set_mark(i, i in prev)
        for d in list(self.band):
            if d.id in dids:  # a derived picture made since was not part of the action
                self.band.set_marked(d.id, d.id in dprev)
        self._marks_changed_everywhere()
        note = f" ({later} later change{'s' if later != 1 else ''} replaced)" if later else ""
        self.say(f"Undid Mark all / Unmark all: {len(prev) + len(dprev & dids)} marked again{note}")

    # -- rotation (clockwise quarter turns; a copy writes it into the EXIF of the copy only) --------
    def _rotation_of(self, i: int) -> int:
        return self.rotations.get(i, 0)

    def _views(self) -> list:
        return [v for v in (self.viewer, self.filmstrip, self.review, self.compare) if v is not None]

    def _rotation_targets(self) -> list[int]:
        if self._compare_shown():
            f = self.compare.focused_index()
            return [f] if f >= 0 else []
        shown = self._review_shown_index()
        if shown is _NOT_IN_REVIEW:
            return [self.catalog.index] if len(self.catalog) else []
        if getattr(getattr(self.review, "stack", None), "currentIndex", lambda: 0)() == 2 and shown is not None:
            return [shown]  # the grid's zoom page: the picture it shows
        picks = self._review_picks()
        return picks if picks else ([] if shown is None else [shown])

    def rotate(self, delta: int) -> None:
        """Turn the picture(s) in view by `delta` quarter turns (-1 left, +1 right): the viewer's picture, the
        review's selected pictures, compare's focused cell."""
        targets = self._rotation_targets()
        if not targets:
            return
        for i in targets:
            t = (self.rotations.get(i, 0) + delta) % 4
            if t:
                self.rotations[i] = t
            else:
                self.rotations.pop(i, None)
        for i in targets:
            self._story_follows_turn(i)
            for v in self._views():
                v.rotation_changed(i)
        d = self._current_derived()
        if d is not None and d.kind == dv.STORY and d.source in targets:
            self._render_current()  # the story is drawn from the turned picture: draw it again
        side = "right" if delta > 0 else "left"
        if len(targets) == 1:
            t = self.rotations.get(targets[0], 0)
            name = self.catalog.pictures[targets[0]].stem
            self.say(f"Turned {name} {side}: now {t * 90}° clockwise" if t else f"Turned {name} {side}: upright again")
        else:
            self.say(f"Turned {len(targets)} pictures {side}")

    def _copy_rotations(self, pictures) -> dict:
        """Picture.primary -> quarter turns, for the pictures being copied that have a turn."""
        return self._copy_table(pictures, self.rotations)

    def _copy_table(self, pictures, table: dict) -> dict:
        """Picture.primary -> the table's value, for the pictures being copied that have one."""
        want = {id(p) for p in pictures}
        return {p.primary: table[i] for i, p in enumerate(self.catalog.pictures) if table.get(i) and id(p) in want}

    def _derived_pairs(self) -> list:
        """(source Picture, Derived) for each MARKED derived picture, in band order."""
        pics = self.catalog.pictures
        return [(pics[d.source], d) for d in self.band if d.marked and 0 <= d.source < len(pics)]

    # -- crop (X) and story fit (S): Enter makes a derived picture; the original is never touched -----------------
    def _native_of(self, i: int):
        """Picture i's un-turned native (w, h) as far as the prefetcher's info says, else None."""
        return info_native_size(self.prefetcher.info(i), None) if 0 <= i < len(self.catalog) else None

    def _can_edit(self) -> bool:
        return (self.stack.currentIndex() == 0 and not self._compare_shown() and len(self.catalog) > 0
                and self.viewer.has_image)

    def _ensure_crop_overlay(self):
        if self._crop_overlay is None:
            from spuza.ui.crop_overlay import CropOverlay  # lazy: nothing is built until a crop is made

            ov = CropOverlay(self.viewer.image_item())
            ov.changed.connect(self._rect_touched)  # a drag, move or resize (never a programmatic set_rect_frac)
            self.viewer.pixmap_changed.connect(lambda *_a: ov.sync())
            self._crop_overlay = ov
        return self._crop_overlay

    def _ensure_story_editor(self):
        if self._story_editor is None:
            from spuza.ui.story_overlay import StoryEditor

            ed = StoryEditor(self.viewer.viewport())
            ed.applied.connect(self._apply_story)
            ed.cancelled.connect(lambda: self._end_edit(announce=True))
            self._story_editor = ed
        return self._story_editor

    def _show_applied_crop(self) -> None:
        """Originals carry no crop any more: the passive outline is only ever cleared (O(1) when never built)."""
        if self._crop_overlay is not None:
            self._crop_overlay.show_applied(None)

    def _prepare_edit(self) -> None:
        if self.viewer.zoomed:
            self.viewer.toggle_zoom()  # both editors work on the fitted picture

    def _edit_on_source(self) -> bool:
        """A derived picture is current: while the edit is open the viewer shows the SOURCE's own pixels. False when
        they are not on screen (the edit does not start)."""
        if self._current_derived() is None:
            return True
        self._edit_view = True
        self._render_current()
        if self.viewer.has_image:
            return True
        self._edit_view = False
        self._render_current()
        self.say("The original is not decoded yet: try again in a moment")
        return False

    def start_crop(self) -> None:
        if self._edit is not None:
            return
        if not self._can_edit():
            self.say("Crop needs a picture on screen")
            return
        self._prepare_edit()
        i = self.catalog.index
        d = self._current_derived()
        redo = d if d is not None and d.kind == dv.CROP else None  # X on a derived crop re-crops it; else a new one
        if not self._edit_on_source():
            return
        pm = self.viewer.image_item().pixmap()
        shown = (pm.width(), pm.height())
        self._crop_native = info_native_size(self.prefetcher.info(i), shown) or shown
        self._ensure_crop_overlay().begin(redo.crop if redo is not None else None)
        self._rect_auto = False
        self._edit = "crop"
        self._edit_target = redo.id if redo is not None else None
        self.ratio_strip.setVisible(True)
        self._sync_chrome()
        self._set_ratio(redo.ratio if redo is not None and redo.ratio is not None else ratio_mod.FREE, reshape=False)

    # -- the ratio of the open crop edit (keys 1-8, V, 0 and the strip's chips) ---------------------------------
    def _rect_touched(self) -> None:
        self._rect_auto = False

    def _crop_status(self) -> None:
        label = ratio_mod.label(self._crop_ratio, self._rotation_of(self.catalog.index))
        again = " replaces it" if self._edit_target is not None else " adds it after the original"
        if self._crop_ratio.entry == 1:
            self.say(f"Crop free: drag on the picture. 1-8 ratio, 0 resets. Enter{again}, Esc cancels, "
                     "Backspace clears the rectangle")
        else:
            self.say(f"Crop {label}: drag to move, corners to size. 1-8 ratio, V turns it, 0 resets. "
                     f"Enter{again}, Esc cancels, Backspace clears the rectangle")

    def _set_ratio(self, r, reshape: bool = True) -> None:
        """Make `r` the open crop's ratio: lock the overlay to it and (reshape) bring the rectangle to it, keeping
        its centre and area, or the largest centred one when there is none yet. Free keeps the rectangle."""
        w, h = self._crop_native
        self._crop_ratio = r
        aspect = ratio_mod.frame_aspect(r, w, h)
        ov = self._crop_overlay
        turns = self._rotation_of(self.catalog.index)
        ov.set_aspect(aspect, (w, h), ratio_mod.label(r, turns))
        if reshape and aspect is not None:
            cur = ov.rect_frac()
            if cur is None or self._rect_auto:  # still the untouched largest one: the new ratio's largest, centred
                ov.set_rect_frac(ratio_mod.largest_centred(aspect, w, h))
                self._rect_auto = True
            else:  # D5: keep the centre and the area
                ov.set_rect_frac(ratio_mod.reshape(cur, aspect, w, h))
        self.ratio_strip.set_state(r, turns)
        self._crop_status()

    def _pick_ratio(self, entry: int) -> None:
        if self._edit != "crop":
            return
        w, h = self._crop_native
        self._set_ratio(ratio_mod.opening(entry, w, h, self._rotation_of(self.catalog.index)))

    def _turn_ratio(self) -> None:
        if self._edit == "crop":
            self._set_ratio(ratio_mod.flip(self._crop_ratio))

    def _reset_ratio(self) -> None:
        if self._edit != "crop":
            return
        w, h = self._crop_native
        self._crop_overlay.set_rect_frac(ratio_mod.largest_centred(ratio_mod.frame_aspect(self._crop_ratio, w, h), w, h))
        self._rect_auto = True
        self._crop_status()

    def start_story(self) -> None:
        if self._edit is not None:
            return
        if not self._can_edit():
            self.say("Story fit needs a picture on screen")
            return
        self._prepare_edit()
        i = self.catalog.index
        d = self._current_derived()
        redo = d if d is not None and d.kind == dv.STORY else None  # S on a derived story re-fits it; else a new one
        if not self._edit_on_source():
            return
        pm = self.viewer.image_item().pixmap()
        shown = (pm.width(), pm.height())
        native = info_native_size(self.prefetcher.info(i), shown) or shown
        self._story_native = native
        self._ensure_story_editor().begin(pm, self._rotation_of(i), redo.story if redo is not None else None, native)
        self._edit = "story"
        self._edit_target = redo.id if redo is not None else None
        self._sync_chrome()
        self.say("Story fit 9:16: drag to move, wheel or + - to zoom, 0 resets. "
                 + ("Enter replaces it, Esc cancels" if redo is not None else "Enter adds it after the original, Esc cancels"))

    def _story_follows_turn(self, i: int) -> None:
        """A story is placed in the TURNED frame, so a new turn re-fits it: an open story edit is re-begun with the
        new turns (its Story re-clamped for the new frame), and every derived story of the picture is re-clamped for
        it. A crop needs nothing: it is in the un-turned frame."""
        from spuza import story as st

        if self._edit == "crop" and i == self.catalog.index:
            self._set_ratio(self._crop_ratio, reshape=False)  # the chip, label and status name the ratio as turned
        if self._edit == "story" and i == self.catalog.index:
            ed = self._story_editor
            ed.begin(self.viewer.image_item().pixmap(), self._rotation_of(i), ed.story(), self._story_native)
        native = info_native_size(self.prefetcher.info(i), None)
        if native:
            w, h = native if self._rotation_of(i) % 2 == 0 else (native[1], native[0])
            for d in self.band.of(i):
                if d.kind == dv.STORY:
                    self.band.replace(d.id, story=st.clamp(d.story, w, h))
            self.filmstrip.band_changed()

    def _end_edit(self, announce: bool = False, restore: bool = True) -> None:
        """Leave the edit mode without applying anything. `restore`: show the derived picture again if the edit had
        put the source on screen (the callers that render next pass False)."""
        kind, self._edit = self._edit, None
        self._edit_target = None
        if kind:
            self._sync_chrome()  # the pill comes back in Lights out
        if kind == "crop":
            self._crop_overlay.end()
            self._crop_overlay.set_aspect(None, (0, 0))
            self._crop_ratio = ratio_mod.FREE
            self.ratio_strip.setVisible(False)
            self._show_applied_crop()
        elif kind == "story":
            self._story_editor.end()
        if self._edit_view:
            self._edit_view = False
            if restore:
                self._render_current()
        if kind and announce:
            self.say("Cancelled: nothing changed")

    def _band_changed(self) -> None:
        """The Band was changed (a picture made, replaced, removed or restored): the selection and the strip follow."""
        self._current_derived()  # drops a selection that no longer exists
        self.filmstrip.band_changed()

    def _derived_done(self, d, text: str) -> None:
        self._cur_id = d.id
        self._band_changed()
        self._render_current()
        self.say(text)

    def _apply_crop(self) -> None:
        from spuza.crop import normalise

        i = self.catalog.index
        rect = normalise(self._crop_overlay.rect_frac())
        r = self._crop_ratio
        target = self._edit_target
        self._end_edit(restore=False)
        name = self.catalog.pictures[i].stem
        if rect is None:  # no rectangle: nothing is made, nothing changes
            self._render_current()
            self.say(f"No rectangle drawn: nothing was added for {name}")
            return
        ratio = None if r.entry == 1 else r  # Free is None
        try:
            d = self.band.replace(target, crop=rect, ratio=ratio) if target is not None else None
            if d is None:
                d = self.band.add(i, dv.CROP, crop=rect, ratio=ratio)
                target = None
        except ValueError as exc:
            self._render_current()
            self.say(f"Cannot crop {name}: {exc}")
            return
        head, rows = derived_lines(d, name, self._native_of(i), self._rotation_of(i))
        lab = rows[0].lower() if d.ratio is None or d.ratio.entry in (1, 2) else rows[0]
        size = rows[1] if len(rows) > 1 else ""
        what = "" if target is None else " again"
        tail = "It replaced the earlier crop" if target is not None else "It is a new picture after the original"
        self._derived_done(d, f"Cropped {name}{what}: {lab}, {size}. {tail}; the original stays as it is")

    def _apply_story(self) -> None:
        if self._edit != "story":
            return
        i = self.catalog.index
        story = self._story_editor.story()
        target = self._edit_target
        self._end_edit(restore=False)
        name = self.catalog.pictures[i].stem
        d = self.band.replace(target, story=story) if target is not None else None
        if d is None:
            try:
                d = self.band.add(i, dv.STORY, story=story)
            except ValueError as exc:
                self._render_current()
                self.say(f"Cannot fit {name}: {exc}")
                return
            tail = "It is a new picture after the original"
        else:
            tail = "It replaced the earlier fit"
        self._derived_done(d, f"Story fit on {name}: 9:16. {tail}; the original stays as it is")

    def remove_derived(self) -> None:
        """Backspace on a derived picture: take it off the filmstrip. The selection moves to the previous row."""
        d = self._current_derived()
        if d is None:
            self.say("Nothing to remove here")
            return
        pos = self._row_pos()
        prev = self._rows()[pos - 1] if pos else (d.source, None)
        name = self.catalog.pictures[d.source].stem
        tag = dv.tag(d, self._rotation_of(d.source))
        self.band.remove(d.id)
        self._cur_id = prev[1] if prev[0] == d.source else None
        self._band_changed()
        self._render_current()
        self.say(f"Removed the {'story' if d.kind == dv.STORY else 'crop'} picture {tag} of {name}; the original is untouched")

    def _edit_key(self, e) -> bool:
        """While a crop or story edit is open: True when the key was taken here. Moving keys cancel the edit and
        then fall through (False) so the move happens; marks, F, C... fall through untouched."""
        k, auto = e.key(), e.isAutoRepeat()
        crop = self._edit == "crop"
        if k in (Qt.Key_Return, Qt.Key_Enter):
            if not auto:
                self._apply_crop() if crop else self._story_editor.apply()
        elif k == Qt.Key_Escape:
            if not auto:
                self._end_edit(announce=True)  # never also leaves full-bleed or the review
        elif k == Qt.Key_Backspace:
            if not auto:
                if crop:  # clear the rectangle: Enter then makes nothing
                    self._crop_overlay.set_rect_frac(None)
                    self._rect_auto = False
                    self.say("Rectangle cleared: draw a new one, or Esc to cancel")
                else:
                    self._story_editor.reset()
        elif k in (Qt.Key_X, Qt.Key_S) or k == Qt.Key_Z:
            pass  # either editor's key again, and Z: nothing while editing
        elif crop and k in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus):
            pass
        elif crop and Qt.Key_1 <= k <= Qt.Key_8:
            if not auto:
                self._pick_ratio(k - Qt.Key_0)
        elif crop and k == Qt.Key_9:
            pass
        elif crop and k == Qt.Key_V:
            if not auto:
                self._turn_ratio()
        elif crop and k == Qt.Key_0:
            if not auto:
                self._reset_ratio()
        elif not crop and k in (Qt.Key_Plus, Qt.Key_Equal):
            self._story_editor.zoom_step(1)
        elif not crop and k == Qt.Key_Minus:
            self._story_editor.zoom_step(-1)
        elif not crop and k == Qt.Key_0:
            self._story_editor.reset()
        elif not crop and Qt.Key_1 <= k <= Qt.Key_9:
            pass  # the story fit has no use for them, and a pill must not take them while it is open
        else:
            if k in (Qt.Key_Right, Qt.Key_J, Qt.Key_Left, Qt.Key_K):
                self._end_edit(restore=False)  # the move that follows renders
            return False
        return True

    # -- rendering --------------------------------------------------------------------------------
    def _render_current(self) -> None:
        i, pic = self.catalog.index, self.catalog.current
        if pic is None:
            self.viewer.show_message(f"No pictures found in {self.root}")
        else:
            img = self.prefetcher.cached(i)
            reason = self.prefetcher.failed_reason(i)
            d = None if self._edit_view else self._current_derived()
            if img is not None:
                self.viewer.show_image(i, pic, img, self.prefetcher.info(i), derived=d)
            elif reason:
                self.viewer.show_error(i, pic, reason)
            else:
                self.viewer.show_placeholder(i, pic)
            self._overlays_for(i, img)
            self._show_applied_crop()
            if img is not None or reason:
                self._say_banner()
        self.filmstrip.set_current(i, self._cur_id)
        self.refresh_marks()

    def _overlays_for(self, i: int, img) -> None:
        """Peaking mask and AF ring for the picture just shown (both no-ops while off or in classic)."""
        if self.peaking is not None:
            self.peaking.show(i, None if self._derived_view() else img)  # its mask is the source frame's
        if self.marker is not None:
            self.marker.set_point(self._focus_frac(i) if img is not None and not self.viewer.sensor_shown else None)

    def _derived_view(self) -> bool:
        """A derived picture (not the source under an edit) is on screen: the peaking mask and the AF point belong to
        the source's frame, so neither is shown over it."""
        return self._cur_id is not None and not self._edit_view and self._current_derived() is not None

    def _peak_image(self, i: int):
        return None if self._derived_view() else self.prefetcher.cached(i)

    def _say_banner(self) -> None:
        if self._banner:
            banner, self._banner = self._banner, None
            self.say(banner)

    def refresh_marks(self) -> None:
        """Everything that depends on the current picture or the marked set."""
        cat, i = self.catalog, self.catalog.index
        pic = cat.current
        d = self._current_derived()
        marked = d.marked if d is not None else (bool(pic) and cat.is_marked(i))
        shown = self._review_shown_index()
        if self._compare_shown():  # lit for the focused cell
            f = self.compare.focused_index()
            self.buttons["mark"].setEnabled(f >= 0)
            self.buttons["mark"].setChecked(f >= 0 and cat.is_marked(f))
        elif shown is _NOT_IN_REVIEW:
            self.buttons["mark"].setEnabled(True)
            self.buttons["mark"].setChecked(marked)
        else:  # lit for what the user sees: the grid selection (or its zoom page)
            self.buttons["mark"].setEnabled(shown is not None)
            self.buttons["mark"].setChecked(shown is not None and cat.is_marked(shown))
        total_marked = self._marked_total()
        self._refresh_sidebar_counts()
        self._refresh_sidebar_pills()
        self.pill.set_state(marked, f"{i + 1} / {len(cat)}" if pic is not None else "0 / 0")
        self._sync_chrome()  # the page, an edit or compare may have changed whether the pill belongs
        self._pills_follow()
        if pic is not None:
            name, kind = (pic.stem, pic.kind) if d is None else (
                f"{pic.stem} ({dv.tag(d, self._rotation_of(i))})", "story" if d.kind == dv.STORY else "crop")
            self.viewer.strip.set_state(cat.position_text(), marked, name, kind, total_marked)
            self._show_inspector(marked=marked)
        else:
            self.inspector.set_picture(None)
        self.refresh_status()

    def _show_inspector(self, info=None, marked=None) -> None:
        cat, i = self.catalog, self.catalog.index
        pic = cat.current
        if pic is None:
            return
        d = self._current_derived()
        if marked is None:
            marked = d.marked if d is not None else cat.is_marked(i)
        if d is not None:
            head, rows = derived_lines(d, pic.primary.name, self._native_of(i), self._rotation_of(i))
            self.inspector.set_derived(pic, head, rows, marked)
        else:
            self.inspector.set_picture(pic, info if info is not None else self.prefetcher.info(i), marked,
                                       self.prefetcher.failed_reason(i))

    def refresh_status(self) -> None:
        cat = self.catalog
        d = self._current_derived()
        self.lbl_pos.setText(cat.position_text() if d is None else
                             f"{cat.position_text()} {DOT} {'story' if d.kind == dv.STORY else 'crop'} {dv.tag(d, self._rotation_of(d.source))}")
        self.lbl_marked.setText(f"{self._marked_total()} marked {DOT} {human_size(cat.marked_bytes)}")

    def _on_ready(self, i: int, _img, _info) -> None:
        self.filmstrip.item_changed(i)
        if i == self.catalog.index:
            self._render_current()
        else:
            self.refresh_status()

    def _on_sized(self, i: int, info) -> None:
        """The picture's own size came in late (Prefetcher.sized): refresh the inspector, nothing else."""
        cat = self.catalog
        if i == cat.index and 0 <= i < len(cat):
            self._show_inspector(info=info)

    def _on_failed(self, i: int, _reason: str) -> None:
        self.filmstrip.item_changed(i)
        if i == self.catalog.index:
            self._render_current()

    def _on_shown(self, i: int) -> None:
        if self._t_key is not None and i == self.catalog.index:
            ms = (time.perf_counter() - self._t_key) * 1000
            self._t_key = None
            self.step_timed.emit(i, ms)

    def say(self, text: str) -> None:
        self.last_message = text
        if self.is_bleed:  # the status bar is hidden in full-bleed; use the strip
            self.viewer.strip.show_note(text, 5000)
            self.viewer.reveal_strip()
        elif self.lights_out:  # ... and in Lights out: a note over the photo, above the pill, for five seconds
            self._show_toast(text)
        else:
            self.statusBar().showMessage(text, 5000)

    TOAST_MS = 5000  # how long a status message stays over the photo in Lights out (the status bar's own 5 s)

    def _show_toast(self, text: str) -> None:
        t = self._toast
        t.setText(text)
        t.show()
        self._place_toast()
        t.raise_()
        self._toast_timer.start(self.TOAST_MS)

    def _place_toast(self) -> None:
        t = self._toast
        if not t.isVisible():
            return
        room = max(200, self.stack.width() - 80)  # one line when it fits, else wrapped to the room there is
        t.setFixedWidth(min(room, t.fontMetrics().horizontalAdvance(t.text()) + 40))
        t.setFixedHeight(t.heightForWidth(t.width()))
        x = max(0, (self.stack.width() - t.width()) // 2)
        y = max(0, self.stack.height() - t.height() - self.pill.height() - 24 - 8)  # above the pill's place
        t.move(x, y)

    # -- modes ------------------------------------------------------------------------------------
    def set_bleed(self, on: bool) -> None:
        if on == self.is_bleed or (on and self.stack.currentIndex() == 1):
            self.buttons["fullscreen"].setChecked(self.is_bleed)  # full-bleed is inert in review
            return
        self.is_bleed = on
        self._sync_chrome()
        self.viewer.set_bleed(on)  # hidden; pointer movement reveals it
        if self._compare_strip is not None:
            self._compare_strip.setVisible(on)
        if on:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        else:
            self.showMaximized() if self._was_maximized else self.showNormal()
        self.buttons["fullscreen"].setChecked(on)
        if self.stack.currentIndex() == 1:
            self._focus_review()
        else:
            self.setFocus()

    # -- layout: A (the quiet bar, default) and B (Lights out), independent of full screen --------------------------
    def set_lights_out(self, on: bool) -> None:
        """Switch between Direction A and B and remember it. Inside full screen only the saved mode flips: the screen
        stays the photo alone, and the new mode's chrome appears when full screen ends."""
        on = bool(on)
        if on != self.lights_out:
            self.lights_out = on
            self.prefs.setValue(settings.LIGHTS_OUT, on)
            self.prefs.sync()
        self._sync_chrome()
        self.refresh_actions()

    def _overlay_open(self) -> bool:
        return self._shortcuts is not None and self._shortcuts.isVisible()

    def _pill_wanted(self) -> bool:
        """The pill floats over the single view in B. It stays off review (the grid has its own header and the pill
        would cover its bottom row), off compare (it would cover the cells) and off an open crop or story edit (it
        would cover the handles and the frame), and off full screen, which shows the photo alone."""
        return (self.lights_out and not self.is_bleed and self.stack.currentIndex() == 0
                and not self._compare_shown() and self._edit is None)

    def _sync_chrome(self) -> None:
        """Show what the layout, full screen and the page ask for. Called wherever one of the three changes."""
        chrome = not self.is_bleed and not self.lights_out  # A outside full screen
        for w in (self.quiet_bar, self.sidebar, self.inspector, self.filmstrip, self.statusBar()):
            w.setVisible(chrome)
        self.toolbar.setVisible(False)  # the buttons stay for state; the quiet bar is what shows
        self.toolbar2.setVisible(False)
        if sys.platform != "darwin":  # the native macOS bar is not a window widget and hides itself in full screen
            self.menuBar().setVisible(not self.is_bleed)
        if not (self.lights_out and not self.is_bleed):
            self._toast.hide()
        show = self._pill_wanted()
        self.pill.setVisible(show)
        if show:
            self.pill.reposition()
            self.pill.raise_()

    def _build_overflow(self) -> None:
        """The bar's ellipsis menu holds the menu bar's own QActions (so label, key, check, enabled and the advanced
        flags are the same objects as in the View menu)."""
        menu = self.quiet_bar.overflow_menu()
        for aid in OVERFLOW:
            if aid is None:
                menu.addSeparator()
            else:
                menu.addAction(self.menu_actions[aid])

    def toggle_review(self) -> None:
        if self._compare_shown():
            return  # leave compare first (Esc); the Review button is disabled meanwhile
        self._end_edit()
        if self.stack.currentIndex() == 1:
            self.close_review()
        else:
            self.open_review()

    def _default_review_factory(self, catalog, prefetcher=None, parent=None):
        from spuza.ui.review import ReviewGrid  # lazy: written beside this module, imported on first use

        return ReviewGrid(catalog, prefetcher, parent)

    def open_pill_set(self, name: str) -> None:
        """A pill row of the sidebar: review shows the pictures carrying that pill, big, from the first one."""
        pill = self.pill_book.by_name(name)
        if pill is None:
            self.say(f"No pill called {name}")
            return
        self.open_review("pill", pill.name)

    def _pic_has_pill(self, i: int, name: str) -> bool:
        """Does catalog picture i carry the pill `name` (case-folded)? The review's set lookup."""
        pics = self.catalog.pictures
        if not 0 <= i < len(pics):
            return False
        key = str(name).casefold()
        return any(n.casefold() == key for n in self.pill_index.get(pics[i]))

    def open_review(self, kind: str = "marked", name: str | None = None) -> None:
        """Show review on a set: the marked pictures (kind "marked") or those carrying pill `name` (kind "pill"),
        on the zoom page at the viewer's picture when it is in the set, else at the set's first picture."""
        if self._compare_shown():
            return
        if self.review is None:
            factory = self.review_factory or self._default_review_factory
            try:
                self.review = factory(self.catalog, prefetcher=self.prefetcher, parent=self)
            except Exception as exc:  # noqa: BLE001 - e.g. the module is missing; stay on the viewer
                self.say(f"Review is not available: {exc}")
                return
            if self._review_slot is not None:  # compare was built first and kept the slot
                self.stack.removeWidget(self._review_slot)
                self._review_slot.deleteLater()
                self._review_slot = None
            self.review.rotation_of = self._rotation_of
            if hasattr(self.review, "pills_of"):  # a fake review in a test may not know about pills
                self.review.pills_of = self._pill_colors_of
                self.review.pill_has = self._pic_has_pill  # the pill sets' membership
            if hasattr(self.review, "stars_of"):
                self.review.stars_of = self._stars_of
            pill_key = getattr(self.review, "pill_key", None)
            if pill_key is not None:
                pill_key.connect(self._pill_key)
            self.stack.insertWidget(1, self.review)
            self.review.open_index.connect(self._review_open_index)
            self.review.marks_changed.connect(self._review_marks_changed)
            self.review.closed.connect(self.close_review)
            note = getattr(self.review, "status", None)
            if note is not None:
                note.connect(self.say)
        shown = self._review_shown_index() if self.stack.currentIndex() == 1 else _NOT_IN_REVIEW
        prefer = shown if isinstance(shown, int) else (self.catalog.index if len(self.catalog) else None)
        if hasattr(self.review, "min_stars"):
            self.review.min_stars = self._min_stars  # open_set lists the set already narrowed
        opener = getattr(self.review, "open_set", None)
        if opener is not None:
            opener(kind, name, prefer)
        else:  # a fake review in a test lists the marked pictures only
            self.review.refresh()
        self.sidebar.set_open(kind, name)
        self.stack.setCurrentIndex(1)
        self.buttons["review"].setChecked(True)
        self.buttons["fullscreen"].setEnabled(False)  # full-bleed is inert in review: show it
        self.refresh_marks()
        self._focus_review()

    def _review_target(self):
        """The widget of the review page that handles keys: its zoom or list view, else the page."""
        r = self.review
        for name in ("zoom_view", "view"):
            w = getattr(r, name, None)
            if w is not None and w.isVisibleTo(r):
                return w
        return r

    def _focus_review(self) -> None:
        self._review_target().setFocus()

    def _review_shown_index(self):
        """Catalog index the review page shows (selection, or the zoom page's picture), None when
        nothing is selected, _NOT_IN_REVIEW when the viewer is what is shown (or the review is a fake)."""
        r = self.review
        if self.stack.currentIndex() != 1 or not hasattr(r, "selected_index"):
            return _NOT_IN_REVIEW
        if getattr(getattr(r, "stack", None), "currentIndex", lambda: 0)() == 2:
            z = getattr(r, "_zoom_index", None)
            if z is not None:
                return z
        return r.selected_index()

    def _select_in_review(self, i: int) -> None:
        """A picture was picked while review is shown: select it in the grid if it is marked, else ignore."""
        r = self.review
        row = r._row_of(i)  # no hasattr guard: if the sibling renames this, the pick must fail loudly
        if row >= 0:
            r.view.setCurrentIndex(r.model.index(row, 0))
        self._focus_review()

    def _control(self, key, viewer_action) -> None:
        """A toolbar control: in review it does what the same key does on the grid, in compare what it does there,
        else drives the viewer."""
        if self._compare_shown():
            self._forward_to_compare(QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
            if self._compare_shown():
                self.buttons["zoom"].setChecked(self.compare.is_zoomed())
            self.refresh_marks()
        elif self._edit is not None and key == Qt.Key_Z:
            self.buttons["zoom"].setChecked(self.viewer.zoomed)  # no zoom while a crop or story edit is open
        elif self.stack.currentIndex() == 1:
            self.forward_to_review(QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
            self.buttons["zoom"].setChecked(self.viewer.zoomed)  # the viewer did not move
            self.refresh_marks()
        else:
            viewer_action()

    def forward_to_review(self, e) -> None:
        """Hand a key event to the review grid (focus may have landed on the window)."""
        self._forwarding = True
        try:
            self._focus_review()
            QApplication.sendEvent(self._review_target(), QKeyEvent(
                e.type(), e.key(), e.modifiers(), e.text(), e.isAutoRepeat(), e.count()))
        finally:
            self._forwarding = False

    def close_review(self) -> None:
        if self._compare_shown():
            return
        self.stack.setCurrentIndex(0)
        self.sidebar.set_open(None)
        self.buttons["review"].setChecked(False)
        self.buttons["fullscreen"].setEnabled(True)
        self.refresh_marks()
        self.setFocus()

    def _review_open_index(self, i: int) -> None:
        self.catalog.go(i)
        self._cur_id = None  # the review lists originals only
        self.close_review()
        self._moved()

    def _review_marks_changed(self) -> None:
        self.filmstrip.viewport().update()
        self.refresh_marks()

    def _copy_destination(self) -> tuple[Path, str, Path | None]:
        """(default destination, a note for the sheet or "", where Choose folder opens or None for 'at the destination').
        No master folder: the usual folder. A master that exists: <master>/MM_DD. A master that is gone (unplugged drive):
        the usual folder and a note; the master is never created."""
        usual = default_copy_dest()
        master_s = settings.read_master_folder(self.prefs)
        master = Path(master_s) if master_s else None
        note, dest = "", usual
        if master is not None:
            if master.is_dir():
                dest = master / month_day(self.today())
            else:
                note = f"Master folder {master} is not available; copying to the usual folder"
                master = None
        last_s = settings.read_last_parent(self.prefs)
        last = Path(last_s) if last_s else None
        start = None
        if last is not None and last.is_dir():
            if master is None or last == master or master in last.parents:
                start = last
        if start is None and master is not None:
            start = master
        return dest, note, start

    def _on_copied_to_chosen(self, folder) -> None:
        """A copy to a folder the user chose finished well: remember its parent for the next Choose folder."""
        try:
            settings.write_last_parent(self.prefs, str(Path(folder).parent))
            self.prefs.sync()
        except Exception as exc:  # noqa: BLE001 - a failed save of a convenience must never break the copy
            log.warning("could not remember the copy folder: %s", exc)

    def _default_copy_sheet_factory(self, pictures, default_dest=None, parent=None, derived=(), pills=None, ratings=None,
                                    start_folder=None, note=""):
        from spuza.ui.copy_sheet import CopySheet  # lazy, see above

        derived = list(derived)
        # a marked derived picture is exported with its source's turns, whether or not the source itself is marked
        turned = list(pictures) + [p for p, _d in derived]
        return CopySheet(pictures, default_dest=default_dest, parent=parent,
                         others=getattr(self.catalog, "others", ()), unreadable=getattr(self.catalog, "unreadable", ()),
                         organise=settings.read_organise(self.prefs), eject_after=settings.read_eject_after(self.prefs),
                         quit_after=settings.read_quit_after(self.prefs), eject_target=self.card_mount,
                         rotations=self._copy_rotations(turned), derived=derived,
                         write_rotation=settings.read_write_rotation(self.prefs),
                         **({"pills": pills} if pills else {}),  # CopySheet takes pills= once spp-copy is merged
                         **({"ratings": ratings} if ratings else {}),
                         **({"start_folder": start_folder} if start_folder else {}),
                         **({"note": note} if note else {}))

    def _copy_pills(self, pictures) -> dict:
        """Picture.primary -> the XMP tags (xmp.PillTag) of the pills it carries, for the pictures being copied that
        carry any the book still knows. Empty when none does."""
        out = {}
        for p in pictures:
            tags = [self.pill_book.tag(n) for n in self.pill_index.get(p) if self.pill_book.by_name(n)]
            if tags:
                out[p.primary] = tags
        return out

    def _copy_ratings(self, pictures) -> dict:
        """Picture.primary -> stars (0 = clear) for the pictures being copied whose stars were set in Spuza. A picture
        never rated in Spuza is left out, so its file's own rating is left alone. Empty when none was."""
        out = {}
        for p in pictures:
            n = self.pill_index.stars(p)
            if n is not None:
                out[p.primary] = n
        return out

    def open_copy(self) -> None:
        marked = self.catalog.marked()
        pairs = self._derived_pairs()  # the marked crops and story fits go too, with or without their originals
        if not marked and not pairs:
            self.say("Nothing is marked yet: press Space on the pictures you want to copy.")
            return
        factory = self.copy_sheet_factory or self._default_copy_sheet_factory
        try:
            extra = {"derived": pairs} if pairs else {}  # a factory that knows nothing of derived pictures is called as before
            pills = self._copy_pills(marked)
            if pills:  # same for pills: only a copy with some carries the kwarg (spp-copy: CopySheet(pills=...))
                extra["pills"] = pills
            ratings = self._copy_ratings(marked)
            if ratings:  # and ratings: only a copy with some stars set in Spuza carries the kwarg
                extra["ratings"] = ratings
            dest, note, start = self._copy_destination()
            if note:  # a factory that knows nothing of notes or start folders is called as before when there are none
                extra["note"] = note
            if start is not None and start != dest:
                extra["start_folder"] = start
            self._sheet = factory(marked, default_dest=dest, parent=self, **extra)
            chosen_signal = getattr(self._sheet, "copied_to_chosen", None)
            if chosen_signal is not None:
                chosen_signal.connect(self._on_copied_to_chosen)
            quit_signal = getattr(self._sheet, "quit_requested", None)
            if quit_signal is not None:
                quit_signal.connect(self.close)  # the normal close path: closeEvent saves and flushes as usual
            (getattr(self._sheet, "open", None) or self._sheet.show)()
        except Exception as exc:  # noqa: BLE001
            self.say(f"Copy is not available: {exc}")

    def _default_prefs_sheet_factory(self, ahead, behind, max_side, parent=None):
        from spuza.ui.prefs_sheet import PrefsSheet  # lazy, like the copy sheet

        return PrefsSheet(ahead, behind, max_side, parent=parent, advanced=features.is_advanced(),
                          threads=settings.read_threads(self.prefs),
                          peaking_threshold=settings.read_peaking_threshold(self.prefs),
                          resume=settings.read_resume_on(self.prefs),
                          organise=settings.read_organise(self.prefs),
                          eject_after=settings.read_eject_after(self.prefs),
                          quit_after=settings.read_quit_after(self.prefs),
                          write_rotation=settings.read_write_rotation(self.prefs),
                          zoom_full_sensor=settings.read_zoom_full_sensor(self.prefs),
                          master_folder=settings.read_master_folder(self.prefs))

    def _show_mode(self) -> None:
        adv = features.is_advanced()
        self.setWindowTitle(f"Spuza (advanced) — {self.root}" if adv else f"Spuza — {self.root}")
        self.advanced_tag.setVisible(adv)

    def set_advanced(self, on: bool) -> None:
        """Switch this run's mode live (title and ADVANCED marker). Saving is the caller's business.

        To classic: the new buttons hide, peaking and the ring turn off, compare closes. To advanced: the saved
        peaking / ring states come back and the buttons show; resume starts (restoring only if nothing was done yet)."""
        on = bool(on)
        if not on:
            self._leave_next_features()
        features.set_mode(features.ADVANCED if on else features.CLASSIC)
        self._show_mode()
        if on:
            self._enter_next_features()
            ctl = self._ensure_resume()
            if ctl is not None and self.catalog.marked_count == 0 and self.catalog.index == 0 and not self.band:
                self._banner = ctl.restore()
                self._band_changed()
        else:
            self._sync_next_buttons()
        self._render_current()

    def open_prefs(self) -> None:
        """Open Preferences (works in review mode too: it is a window-level sheet). The sheet starts from the
        RUNNING prefetcher's window, so a --ahead/--behind override is what the user sees."""
        if self._prefs_sheet is not None and self._prefs_sheet.isVisible():
            return
        pf = self.prefetcher
        self._prefs_seed = features.is_advanced()  # this run's mode, for sheets that do not record their own
        factory = self.prefs_sheet_factory or self._default_prefs_sheet_factory
        try:
            sheet = factory(pf.ahead, pf.behind, pf.max_side, parent=self)
            self._prefs_sheet = sheet
            sheet.accepted.connect(lambda: self._apply_prefs(sheet))
            (getattr(sheet, "open", None) or sheet.show)()
        except Exception as exc:  # noqa: BLE001
            self.say(f"Preferences are not available: {exc}")

    def _apply_prefs(self, sheet) -> None:
        a, b = sheet.values()
        settings.write_window(self.prefs, a, b)
        adv = getattr(sheet, "advanced", None)
        adv = bool(adv()) if callable(adv) else None  # a fake sheet may not know about modes
        changed = adv is not None and adv != bool(getattr(sheet, "seeded_advanced", self._prefs_seed))
        if changed:  # only a flipped box is saved; an unrelated OK never saves a one-run --advanced/--classic
            settings.write_advanced(self.prefs, adv)
        if adv and callable(getattr(sheet, "threads", None)):
            settings.write_threads(self.prefs, sheet.threads())
        peak, remember = None, None  # the advanced rows: written only when changed from their seed
        if adv and callable(getattr(sheet, "peaking_threshold", None)):
            t = sheet.peaking_threshold()
            if t != getattr(sheet, "seeded_peaking", t):
                settings.write_peaking_threshold(self.prefs, t)
                peak = t
        if adv and callable(getattr(sheet, "resume", None)):
            r = bool(sheet.resume())
            if r != getattr(sheet, "seeded_resume", r):
                settings.write_resume_on(self.prefs, r)
                remember = r
        for name, write in (("organise", settings.write_organise), ("eject_after", settings.write_eject_after),
                            ("quit_after", settings.write_quit_after),
                            ("write_rotation", settings.write_write_rotation),
                            ("zoom_full_sensor", settings.write_zoom_full_sensor)):  # classic rows: saved in any mode, if flipped
            getter = getattr(sheet, name, None)
            if callable(getter):
                v = bool(getter())
                if v != getattr(sheet, "seeded_" + {"eject_after": "eject", "quit_after": "quit",
                                                        "write_rotation": "rotation", "zoom_full_sensor": "sensor"}.get(name, name), v):
                    write(self.prefs, v)
        master = getattr(sheet, "master_folder", None)  # classic row: saved in any mode, if changed
        if callable(master):
            m = str(master() or "")
            if m != getattr(sheet, "seeded_master", m):
                settings.write_master_folder(self.prefs, m)
        self.prefs.sync()
        sensor = getattr(sheet, "zoom_full_sensor", None)  # classic row: the viewer follows the sheet at once
        if callable(sensor):
            self.viewer.set_zoom_full_sensor(bool(sensor()))
        if changed:
            self.set_advanced(adv)
        if peak is not None:
            if self.peaking is not None:
                self.peaking.set_threshold(peak)
            self._reset_compare_masks(clear=True)
            self._compare_overlays()
        if remember is not None and self.resume is not None:
            self.resume.set_enabled(remember)
        self.prefetcher.set_window(a, b)
        self.say(f"Prefetch: {a} ahead, {b} behind (about {settings.window_mb(a, b, self.prefetcher.max_side)} MB)")

    # -- advanced features: each is a no-op unless its flag is on -----------------------------------
    def _focus_frac(self, i):
        """Picture `i`'s AF point as a (fx, fy) fraction, or None (flag off, no info, no usable point)."""
        if i is None or i < 0 or not features.enabled("focus_point") or self._derived_view():
            return None
        try:
            fi = getattr(self.prefetcher.info(i), "focus", None)
            return fi.frac() if fi is not None else None
        except Exception:  # noqa: BLE001 - a bad point must never break Z or a step
            return None

    def _marker_follows_frame(self, *_a) -> None:
        """The AF point is in the camera frame (the preview); over the full-sensor image it would be misplaced,
        so the ring is hidden there and comes back with the camera frame."""
        if self.marker is None:
            return
        i = self.viewer.index
        self.marker.set_point(None if self.viewer.sensor_shown else self._focus_frac(i))

    def _zoom_target(self):
        return self._focus_frac(self.viewer.index)

    def _ensure_peaking(self):
        if self.peaking is None:
            from spuza.ui.peaking_overlay import PeakingOverlay  # lazy: numpy stays out of classic start-up

            self.peaking = PeakingOverlay(self.viewer, threshold=settings.read_peaking_threshold(self.prefs),
                                          settle_ms=self.peaking_settle_ms)
        return self.peaking

    def _ensure_marker(self):
        if self.marker is None:
            from spuza.ui.focus_marker import FocusMarker

            self.marker = FocusMarker(self.viewer)
        return self.marker

    def _enter_next_features(self, defer: bool = False) -> None:
        """Start-up, or a live switch to advanced: restore the saved peaking / ring states, show the buttons.

        At start-up (`defer`) a saved-on peaking is built later: importing numpy while the first pictures decode
        delays them (measured: one bench step waited 50-80 ms), so it waits for the prefetch to go idle."""
        if features.enabled("focus_peaking"):
            on = settings.read_peaking_on(self.prefs)
            if on and defer and self.peaking is None:
                self._peaking_wait = True
                self.prefetcher.ready.connect(self._maybe_build_peaking)
                QTimer.singleShot(PEAKING_WARM_MS, self._build_waiting_peaking)
            elif on or self.peaking is not None:  # built only when wanted: numpy stays unimported while P is off
                self._ensure_peaking().set_on(on)
        if features.enabled("focus_point"):
            on = settings.read_focus_marker(self.prefs)
            if on or self.marker is not None:
                self._ensure_marker().set_on(on)
        self._sync_next_buttons()

    def _maybe_build_peaking(self, *_args) -> None:
        busy = getattr(self.prefetcher, "busy", None)  # a fake without busy() counts as idle
        if self._peaking_wait and not (busy() if callable(busy) else False):
            self._build_waiting_peaking()

    def _build_waiting_peaking(self) -> None:
        if not self._peaking_wait:
            return
        self._peaking_wait = False
        if features.enabled("focus_peaking") and settings.read_peaking_on(self.prefs) and self.peaking is None:
            self._ensure_peaking().set_on(True)
            if len(self.catalog):
                self.peaking.show(self.catalog.index, self._peak_image(self.catalog.index))
            self._sync_next_buttons()

    def _leave_next_features(self) -> None:
        """A live switch to classic (run BEFORE the mode flips: the ring ignores set_on in classic)."""
        if self._compare_shown():
            self.compare.close_compare()
        if self.resume is not None:
            self.resume.flush()
        if self.marker is not None:
            self.marker.set_on(False)
        if self.peaking is not None:
            self.peaking.set_on(False)

    def _sync_next_buttons(self) -> None:
        for name, _label, _cap, flag in NEXT_BUTTONS:
            self._next_actions[name].setVisible(features.enabled(flag))
        nb = self.next_buttons
        nb["compare"].setChecked(self._compare_shown())
        nb["peaking"].setChecked(self.peaking is not None and self.peaking.is_on())
        nb["afpoint"].setChecked(self.marker is not None and self.marker.is_on())
        self.refresh_actions()

    # -- peaking (P) and the AF ring (O)
    def toggle_peaking(self) -> None:
        if not features.enabled("focus_peaking"):
            self._sync_next_buttons()
            return
        self.set_peaking(not self._ensure_peaking().is_on())

    def set_peaking(self, on: bool) -> None:
        ov = self._ensure_peaking()
        ov.set_on(on)
        on = ov.is_on()
        settings.write_peaking_on(self.prefs, on)
        self.prefs.sync()
        if on and len(self.catalog):
            ov.show(self.catalog.index, self._peak_image(self.catalog.index))
        self._reset_compare_masks(clear=True)
        self._compare_overlays()
        self._sync_next_buttons()
        self.say("Focus peaking: on (red edges are sharp)" if on else "Focus peaking: off")

    def toggle_marker(self) -> None:
        if not features.enabled("focus_point"):
            self._sync_next_buttons()
            return
        self.set_marker(not self._ensure_marker().is_on())

    def set_marker(self, on: bool) -> None:
        mk = self._ensure_marker()
        mk.set_on(on)
        on = mk.is_on()
        settings.write_focus_marker(self.prefs, on)
        self.prefs.sync()
        frac = self._focus_frac(self.viewer.index)
        mk.set_point(frac)
        self._sync_next_buttons()
        if not on:
            self.say("AF point: hidden")
        else:
            self.say("AF point: shown" if frac is not None else "AF point: on (none known for this picture)")

    # -- resume (U starts fresh)
    def _ensure_resume(self):
        if self.resume is None and self._resume_allowed and features.enabled("resume_session"):
            from spuza.session import SessionStore
            from spuza.ui.resume import ResumeController

            store = SessionStore()  # builds a path only; the folder is made on the first save
            self.resume = ResumeController(self.catalog, self.root, store, parent=self, rotations=self.rotations,
                                           band=self.band)
            remember = settings.read_resume_on(self.prefs)
            self.resume.set_enabled(remember)
            if remember and not self._pruned:
                self._pruned = True  # once per run, off the GUI thread
                self._prune_thread = threading.Thread(target=self._prune, args=(store,), name="spuza-prune",
                                                      daemon=True)
                self._prune_thread.start()
        return self.resume

    @staticmethod
    def _prune(store) -> None:
        try:
            store.prune()  # sessions older than 30 days
        except Exception:  # noqa: BLE001 - housekeeping must never reach the user
            pass

    def start_fresh(self) -> None:
        ctl = self._ensure_resume()
        self._markall_undo = None  # U has its own undo; a stale Mark all snapshot must not undo over it
        if ctl is None:
            return
        text = ctl.start_fresh()
        self._band_changed()
        if text:
            self.say(text)
        self.filmstrip.viewport().update()
        self.refresh_marks()

    # -- compare (N)
    def _compare_shown(self) -> bool:
        return self._compare_page is not None and self.stack.currentWidget() is self._compare_page

    def _ensure_compare(self):
        if self.compare is None:
            from spuza.ui.compare import CompareView  # lazy: only advanced mode ever builds it

            cv = CompareView(self.catalog, self.prefetcher)
            cv.rotation_of = self._rotation_of
            strip = QLabel()
            self._style_compare_strip(strip)
            strip.setVisible(self.is_bleed)
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            lay.addWidget(cv, 1)
            lay.addWidget(strip)
            if self.review is None and self._review_slot is None:
                self._review_slot = QWidget()  # review keeps page 1 even when it is built after compare
                self.stack.addWidget(self._review_slot)
            self.stack.addWidget(page)
            cv.closed.connect(self._compare_closed)
            cv.marks_changed.connect(self._compare_marks)
            cv.focus_changed.connect(self._compare_focus)
            cv.installEventFilter(self)
            self._cmp_sig = _MaskSignals()
            self._cmp_sig.done.connect(self._on_cmp_mask)
            self._cmp_pool = QThreadPool()
            self._cmp_pool.setMaxThreadCount(1)
            self.compare, self._compare_page, self._compare_strip = cv, page, strip
        return self.compare

    @staticmethod
    def _style_compare_strip(strip) -> None:
        c = theme.current()
        strip.setStyleSheet(f"background: {c.hud}; color: {tk.DARK['text']}; padding: 6px 14px;")  # the HUD is dark in both themes

    def _review_picks(self) -> list[int]:
        fn = getattr(self.review, "selected_indices", None)
        try:
            return list(fn()) if callable(fn) else []
        except Exception:  # noqa: BLE001 - a fake review
            return []

    def open_compare(self) -> None:
        if not features.enabled("compare_view") or not len(self.catalog) or self._compare_shown():
            return
        self._end_edit()
        if self.stack.currentIndex() == 1:  # review: the grid's selection, up to four
            picks = self._review_picks()[:4]
            if len(picks) < 2:
                self.say("Select two or more pictures in the grid (Ctrl-click) to compare them.")
                return
            indices, focus, run, origin = picks, 0, False, "review"
        else:  # the viewer: this picture and the next (the last picture: the previous and this)
            n, i = len(self.catalog), self.catalog.index
            if n < 2:
                self.say("Compare needs at least two pictures.")
                return
            indices, focus = ([i, i + 1], 0) if i + 1 < n else ([i - 1, i], 1)
            run, origin = True, "single"
        cv = self._ensure_compare()
        self._compare_origin = origin
        before = self.stack.currentIndex()
        cv.set_advance_on_mark(self.advance_on_mark)
        self._reset_compare_masks()
        self.stack.setCurrentWidget(self._compare_page)
        cv.open(indices, focus, run)
        if not cv.indices():  # refused (should not happen once the flag is on): stay where we were
            self.stack.setCurrentIndex(before)
            return
        self.buttons["review"].setEnabled(False)
        self.buttons["fullscreen"].setEnabled(True)
        self.filmstrip.set_highlight(cv.indices())
        self._sync_compare()
        self.refresh_marks()
        self.setFocus()

    def _compare_button(self) -> None:
        if self._compare_shown():
            self._forward_to_compare(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_N, Qt.KeyboardModifier.NoModifier))
        else:
            self.open_compare()
        self._sync_next_buttons()

    def _forward_to_compare(self, e) -> None:
        """Hand a key event to the compare view (focus may be on the window)."""
        self._forwarding = True
        try:
            QApplication.sendEvent(self.compare, QKeyEvent(
                e.type(), e.key(), e.modifiers(), e.text(), e.isAutoRepeat(), e.count()))
        finally:
            self._forwarding = False
        self._sync_compare()

    def _sync_compare(self) -> None:
        """Filmstrip highlight, bleed-strip text, zoom button and peaking overlays follow compare's cells."""
        cv = self.compare
        if cv is None or not self._compare_shown():
            return
        self.filmstrip.set_highlight(cv.indices())
        self._compare_strip.setText(cv.status_text())
        self.buttons["zoom"].setChecked(cv.is_zoomed())
        self._compare_overlays()
        self._sync_next_buttons()

    def _compare_marks(self) -> None:
        self.filmstrip.viewport().update()
        self.refresh_marks()
        self._sync_compare()

    def _compare_focus(self, _i: int) -> None:
        self.refresh_marks()
        self._sync_compare()

    def _compare_closed(self, i: int) -> None:
        origin = self._compare_origin
        self._reset_compare_masks()
        self.filmstrip.set_highlight(())
        self.buttons["review"].setEnabled(True)
        self.buttons["zoom"].setChecked(self.viewer.zoomed)
        if origin == "review" and self.review is not None:
            if self.is_bleed:
                self.set_bleed(False)  # review is inert in full-bleed
            self.stack.setCurrentIndex(1)
            self.buttons["fullscreen"].setEnabled(False)
            self.review.refresh()
            self.refresh_marks()
            self._focus_review()
        else:
            self.stack.setCurrentIndex(0)
            if i != self.catalog.index:
                self._cur_id = None  # compare lists originals only: it may have moved on
            self.catalog.go(i)
            self._moved()
            self.setFocus()
        self._sync_next_buttons()

    def _reset_compare_masks(self, clear: bool = False) -> None:
        """Forget which cells have an overlay (results in flight become stale); `clear` also removes them."""
        self._cmp_gen += 1
        if clear and self.compare is not None:
            for i in self._cmp_masked:
                self.compare.set_overlay(i, None)
        self._cmp_masked.clear()

    def _compare_overlays(self) -> None:
        """With peaking on, each fitted compare cell gets its own mask (cached one, else a worker computes it)."""
        cv = self.compare
        if cv is None or not self._compare_shown() or self.peaking is None or not self.peaking.is_on():
            return
        threshold = settings.read_peaking_threshold(self.prefs)
        for i in cv.indices():
            if i in self._cmp_masked:
                continue
            mask = self.peaking.mask_for(i)
            if mask is not None:
                cv.set_overlay(i, mask)
                self._cmp_masked.add(i)
                continue
            img = self.prefetcher.cached(i)
            if img is None or img.isNull():
                continue  # not decoded yet (compare decodes it itself): no overlay for this cell
            self._cmp_masked.add(i)
            self._cmp_pool.start(_MaskJob(self._cmp_sig, self._cmp_gen, i, img, threshold))

    def _on_cmp_mask(self, gen: int, i: int, img) -> None:
        if gen == self._cmp_gen and img is not None and self._compare_shown():
            self.compare.set_overlay(i, img)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.compare and ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key_N:
            QTimer.singleShot(0, self._sync_compare)  # N may have added a cell: the highlight follows
        return super().eventFilter(obj, ev)

    # -- pills: 1-9 toggle, 0 clears, Ctrl+Shift+P manages (digits belong to crop / story while one is open)
    def _pill_pictures(self, indices) -> list:
        pics = self.catalog.pictures
        return [pics[i] for i in indices if 0 <= i < len(pics)]

    def _pill_names_of(self, pic) -> tuple[str, ...]:
        """The names of the pills `pic` carries that the book still knows, in digit order."""
        have = {n.casefold() for n in self.pill_index.get(pic)}
        return tuple(p.name for p in self.pill_book.pills() if p.name.casefold() in have)

    def _current_pill_names(self) -> tuple[str, ...]:
        """The pills the picture(s) in view all carry: the viewer's picture, the review's selection, compare's cell."""
        if not len(self.catalog):
            return ()
        try:
            pics = self._pill_pictures(self._rotation_targets())
        except Exception:  # noqa: BLE001 - a fake review
            return ()
        if not pics:
            return ()
        common = None
        for pic in pics:
            names = set(self._pill_names_of(pic))
            common = names if common is None else common & names
        return tuple(n for n in self._pill_names_of(pics[0]) if n in common)

    def _pill_colors_of(self, i: int) -> list[str]:
        """Catalog index -> colour names of its pills, for the strip's and the grid's dots. Until the read-back has
        finished only pictures changed by hand answer (so painting never opens a file)."""
        pics = self.catalog.pictures
        if not 0 <= i < len(pics):
            return []
        pic = pics[i]
        if not self._pill_ready and pic.primary not in self._pill_touched:
            return []
        return [self.pill_book.by_name(n).color for n in self._pill_names_of(pic)]

    def _pills_follow(self) -> None:
        """The current picture changed (or its pills did): the Lights out pill, the inspector and the tray follow."""
        names = self._current_pill_names()
        pairs = [(n, self.pill_book.by_name(n).color) for n in names]
        self.pill.set_pills(pairs)
        self.inspector.pills = pairs  # drawn by the set_picture that follows, or by set_pills when it is alone
        self.pill_tray.refresh()
        self._stars_follow()

    # -- stars: Ctrl/Cmd+1..5 set, Ctrl/Cmd+0 clears; the bar after the tray clicks the same; the sidebar's
    # "at least N" threshold narrows the open review set (the main viewer and the strip stay unfiltered)
    def _stars_pic(self, pic) -> int:
        """The stars shown for a picture: the Spuza index's value when it has one (0 = cleared), else the 1..5
        rating its file carried when the folder loaded, else 0."""
        n = self.pill_index.stars(pic)
        if n is None:
            n = self._file_stars.get(pic.primary, 0)
        return clamp_stars(n)

    def _stars_of(self, i: int) -> int:
        """Catalog index -> stars 0..5, for the strip, the grid, the threshold. Until the read-back has finished only
        pictures it already told us about answer (so painting never opens a file to make an index key)."""
        pics = self.catalog.pictures
        if not 0 <= i < len(pics):
            return 0
        pic = pics[i]
        if (not self._pill_ready and pic.primary not in self._pill_touched and pic.primary not in self._stars_touched
                and pic.primary not in self._file_stars):
            return 0
        return self._stars_pic(pic)

    def _current_stars(self) -> int:
        """The stars of the picture(s) in view: the viewer's picture, the review's selection (0 when they differ)."""
        if not len(self.catalog):
            return 0
        try:
            pics = self._pill_pictures(self._rotation_targets())
        except Exception:  # noqa: BLE001 - a fake review
            return 0
        got = {self._stars_pic(p) for p in pics}
        return got.pop() if len(got) == 1 else 0

    def _stars_follow(self) -> None:
        n = self._current_stars()
        self.pill.set_stars(n)
        self.inspector.set_stars(n)
        self.star_bar.set_stars(n)

    def _stars_key(self, n: int) -> None:
        """Ctrl/Cmd+n: n stars (0 clears) on the picture(s) in view. Ignored while a crop or story edit or the `?`
        overlay is open, and with no picture."""
        if self._edit is not None or self._overlay_open() or not len(self.catalog):
            return
        pics = self._pill_pictures(self._rotation_targets())
        if not pics:
            self.say("Select a picture first")
            return
        n = clamp_stars(n)
        for p in pics:
            self.pill_index.set_stars(p, n)
        self._stars_changed(pics)
        more = f" · {len(pics)} pictures" if len(pics) > 1 else ""
        if n == 0:
            self.say(f"Stars cleared · {native('Ctrl+0')}{more}")
        else:
            self.say(f"{FILLED * n} {n} star{'s' if n > 1 else ''} · {native(f'Ctrl+{n}')}{more}")

    def _star_bar_rated(self, n: int) -> None:
        self._stars_key(n)

    def _stars_changed(self, pics) -> None:
        for pic in pics:
            self._stars_touched.add(pic.primary)
        self._star_counts = None
        self._mark_pills_dirty()
        self._redraw_pills()

    def _set_min_stars(self, n: int) -> None:
        """The sidebar's threshold chips: only pictures with at least n stars (0: all) in the review's open set."""
        self._min_stars = clamp_stars(n)
        self._star_counts = None
        self.sidebar.set_min_stars(self._min_stars)
        if self.review is not None and hasattr(self.review, "set_min_stars"):
            self.review.set_min_stars(self._min_stars)
        self.refresh_marks()

    def _star_pass(self) -> tuple:
        """(pictures with at least the threshold, {pill name casefolded: how many of them carry it}); cached."""
        if self._star_counts is None:
            per: dict[str, int] = {}
            n_all = 0
            for i, pic in enumerate(self.catalog.pictures):
                if self._stars_of(i) >= self._min_stars:
                    n_all += 1
                    for name in self.pill_index.get(pic):
                        per[name.casefold()] = per.get(name.casefold(), 0) + 1
            self._star_counts = (n_all, per)
        return self._star_counts

    def _refresh_sidebar_counts(self) -> None:
        cat = self.catalog
        shown = None
        if self._min_stars > 0:
            marked = sum(1 for i in range(len(cat.pictures)) if cat.is_marked(i) and self._stars_of(i) >= self._min_stars)
            shown = (self._star_pass()[0], marked)
        self.sidebar.set_counts(len(cat) + len(self.band), self._marked_total(), shown)

    def _pill_hint(self, n: int) -> None:
        self.say(f"No pill on {n} — {native(MANAGE_SEQ)} to make one")

    def _pill_key(self, n: int) -> None:
        """Digit n: 1-9 toggle pill n on the picture(s) in view, 0 clears them. Never reached while a crop or story edit
        is open (keyPressEvent hands those digits to the editor)."""
        if not len(self.catalog):
            return
        pics = self._pill_pictures(self._rotation_targets())
        if n == 0:
            had = [p for p in pics if self.pill_index.get(p)]
            for p in had:
                self.pill_index.clear(p)
            self._pills_changed(pics)
            what = "this picture" if len(pics) == 1 else f"{len(pics)} pictures"
            self.say(f"Pills cleared on {what}" if had else "No pills to clear")
            return
        pill = self.pill_book.by_digit(n)
        if pill is None:
            self._pill_hint(n)
            return
        if not pics:
            self.say("Select a picture first")
            return
        key = pill.name.casefold()
        all_have = all(key in {x.casefold() for x in self.pill_index.get(p)} for p in pics)
        for p in pics:
            has = key in {x.casefold() for x in self.pill_index.get(p)}
            if has == all_have:  # all have it: take it off; else put it on the ones that lack it
                self.pill_index.toggle(p, pill.name)
        self._pills_changed(pics)
        state = "off" if all_have else "on"
        self.say(f"{pill.name} {state}" if len(pics) == 1 else f"{pill.name} {state}: {len(pics)} pictures")

    def _pills_changed(self, pics) -> None:
        """Pictures' pills changed by hand: redraw everything that shows them and queue the (debounced) save."""
        for pic in pics:
            self._pill_touched.add(pic.primary)
        self._pill_counts = self._star_counts = None
        self._mark_pills_dirty()
        self._redraw_pills()

    def _redraw_pills(self) -> None:
        self._pills_follow()
        if self.catalog.current is not None:
            self.inspector.set_pills(self.inspector.pills)
        self.filmstrip.viewport().update()
        if self.review is not None and (getattr(self.review, "set_kind", "marked") == "pill" or self._min_stars > 0):
            self.review.refresh()  # the set is "carries this pill" (and enough stars): a picture may leave it
        elif self.review is not None and hasattr(self.review, "view"):
            self.review.view.viewport().update()
        self._refresh_sidebar_pills()
        self.refresh_actions()

    def _refresh_sidebar_pills(self) -> None:
        """One sidebar row per pill, in the book's order, with how many pictures of this folder carry it."""
        counts = self.pill_counts()
        if self._min_stars > 0:
            per = self._star_pass()[1]
            self.sidebar.set_pills([(p.name, p.color, counts.get(p.name.casefold(), 0), per.get(p.name.casefold(), 0))
                                    for p in self.pill_book.pills()])
        else:
            self.sidebar.set_pills([(p.name, p.color, counts.get(p.name.casefold(), 0)) for p in self.pill_book.pills()])
        self._refresh_sidebar_counts()

    def _snap_pills(self) -> None:
        self._pill_snapshot = [p.name for p in self.pill_book.pills()]

    def _sync_pill_set(self) -> None:
        """Pills were edited: the open pill set follows a pill renamed in place, or closes when its pill is gone."""
        r = self.review
        if r is None or getattr(r, "set_kind", "marked") != "pill":
            return
        name = r.set_name
        if self.pill_book.by_name(name) is not None:
            return
        now = [p.name for p in self.pill_book.pills()]
        prev = self._pill_snapshot
        if name in prev and len(prev) == len(now):  # same number of pills: the one at that place was renamed
            new = now[prev.index(name)]
            r.rename_set(new)
            self.sidebar.set_open("pill", new)
            return
        self.say(f"{name} is gone")
        self.close_review()

    def _mark_pills_dirty(self) -> None:
        self._pill_dirty = True
        self._pill_save_timer.start(PILL_SAVE_MS)

    def _flush_pill_save(self) -> None:
        """Timer slot: write the book now (nine rows) and the index on a worker. Never raises."""
        if not self._pill_dirty:
            return
        self._pill_dirty = False
        try:
            self.pill_book.save()
        except Exception as e:  # noqa: BLE001
            log.warning("could not save the pills: %s", e)
        self._pill_save_pool.start(_PillSaveJob(self.pill_index))

    def _save_pills_now(self) -> None:
        """On close: stop the timer, let a save in flight finish (bounded), then write whatever is still unwritten."""
        self._pill_save_timer.stop()
        self._pill_save_pool.waitForDone(PILL_JOIN_MS)
        if self._pill_dirty:
            self._pill_dirty = False
            for save in (self.pill_book.save, self.pill_index.save):
                try:
                    save()
                except Exception as e:  # noqa: BLE001 - closing must not fail on a full disk
                    log.warning("could not save the pills: %s", e)

    def pill_counts(self) -> dict[str, int]:
        """Pill name (case-folded) -> how many pictures of this folder carry it."""
        if self._pill_counts is None:
            counts: dict[str, int] = {}
            for pic in self.catalog.pictures:
                for n in self.pill_index.get(pic):
                    counts[n.casefold()] = counts.get(n.casefold(), 0) + 1
            self._pill_counts = counts
        return self._pill_counts

    def _pill_count(self, name: str) -> int:
        return self.pill_counts().get(str(name).casefold(), 0)

    def open_pill_manager(self) -> None:
        mgr = self._pill_mgr
        if mgr is not None and mgr.isVisible():
            mgr.raise_()
            mgr.activateWindow()
            return
        from spuza.ui.pill_manager import PillManager  # lazy: built on first use

        self._pill_counts = self._star_counts = None
        mgr = self._pill_mgr = PillManager(self.pill_book, self.pill_index, self._pill_count, len(self.catalog),
                                           parent=self)
        mgr.changed.connect(self._pills_edited)
        mgr.finished.connect(lambda _r: self._pills_edited())
        mgr.open()

    def _pills_edited(self) -> None:
        """The manager made, renamed, recoloured or deleted a pill."""
        self._pill_counts = self._star_counts = None
        self._mark_pills_dirty()
        self._sync_pill_set()
        self._snap_pills()
        self._redraw_pills()

    def _tray_toggled(self, name: str) -> None:
        pill = self.pill_book.by_name(name)
        if pill is not None:
            self._pill_key(pill.digit)

    def _tray_more(self) -> None:
        """The tray's "+N": open the Pills menu under it."""
        menu = getattr(self, "_pills_menu", None)
        if menu is not None:
            menu.popup(self.pill_tray.mapToGlobal(QPoint(0, self.pill_tray.height())))

    # -- read-back: the pills a folder's pictures already carry in their XMP (the file wins over the index)
    def start_pill_readback(self) -> None:
        """(Re)start the read of every picture's pills on a worker. An older read is told to stop and its late results
        are ignored (generation token). At most PILL_READ_MAX pictures, handed back PILL_READ_BATCH at a time."""
        self._pill_cancel.set()
        self._pill_cancel = cancel = threading.Event()
        self._pill_gen += 1
        self._pill_ready = False
        self._pill_touched.clear()  # the file wins over the index for a folder that is loaded again
        self._file_stars.clear()  # (a rating the file carries shows until the index has a value of its own)
        self._stars_touched.clear()
        self._star_counts = None
        pics = list(self.catalog.pictures)
        if not pics:
            self._pill_ready = True
            return
        self._pill_read_pool.start(_PillReadJob(self._pill_sig, self._pill_gen, pics, self.pill_index, cancel))

    def _on_pill_batch(self, gen: int, batch) -> None:
        if gen != self._pill_gen:
            return  # another folder loaded since
        pics = self.catalog.pictures
        changed = rated = False
        for item in batch:
            k, tags = item[0], item[1]
            rating = item[2] if len(item) > 2 else None
            if not 0 <= k < len(pics):
                continue
            if rating is not None and self._file_stars.get(pics[k].primary) != rating:
                self._file_stars[pics[k].primary] = rating  # shown only where Spuza's index has no value of its own
                rated = True
            if tags is None or pics[k].primary in self._pill_touched:
                continue  # a pill changed by hand since this folder loaded wins over the older file
            self.pill_book.adopt(tags)
            self.pill_index.set(pics[k], [t.name for t in tags])
            changed = True
        if changed or rated:
            self._pill_counts = self._star_counts = None
            if changed:
                self._mark_pills_dirty()
                self._snap_pills()
            self._refresh_sidebar_pills()
            if rated:
                self._stars_follow()
                self.filmstrip.viewport().update()
            if self.review is not None and (getattr(self.review, "set_kind", "marked") == "pill"
                                            or (rated and self._min_stars > 0)):
                self.review.refresh()
            elif rated and self.review is not None and hasattr(self.review, "view"):
                self.review.view.viewport().update()

    def _on_pill_finished(self, gen: int, _seen: int) -> None:
        if gen != self._pill_gen:
            return
        self._pill_ready = True
        self._star_counts = None
        self._redraw_pills()

    def wait_pill_readback(self, timeout_ms: int = 5000) -> bool:
        """Block (up to timeout_ms) until the read-back has finished and its results are applied. For tests."""
        end = time.monotonic() + timeout_ms / 1000
        while not self._pill_ready and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.002)
        return self._pill_ready

    # -- menu bar (ui/actions.py is the one table) -------------------------------------------------
    def _build_menus(self) -> None:
        """File / Edit / View / Picture / Help. The QActions DISPLAY their keys (so the native macOS bar shows them
        too) but their shortcut context is WidgetShortcut and they belong to no widget that takes focus, so a key
        press is still handled once, by keyPressEvent or a QShortcut; clicking one runs the same act_* method."""
        self.menu_actions: dict[str, QAction] = {}
        menus = {}
        for name in act.MENUS:
            menus[name] = self.menuBar().addMenu(name)
            menus[name].aboutToShow.connect(self.refresh_actions)
        for a in act.ACTIONS:
            qa = QAction(a.label, self)
            qa.setShortcuts([QKeySequence(k) for k in a.keys])
            qa.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            qa.setShortcutVisibleInContextMenu(True)
            qa.setCheckable(a.checkable)
            qa.triggered.connect(lambda _checked=False, h=a.handler: self._run_action(h))
            menus[a.menu].addAction(qa)
            self.menu_actions[a.id] = qa
        self._pills_menu = menus["Pills"]
        for btn in (*self.buttons.values(), *self.next_buttons.values()):
            btn.toggled.connect(lambda _on=False: self.refresh_actions())
        self.refresh_actions()

    def _run_action(self, handler: str) -> None:
        getattr(self, handler)()
        self.refresh_actions()

    def refresh_actions(self) -> None:
        """Visible (advanced-only flags), enabled and checked in step with the toolbar buttons and the mode."""
        menu_actions = getattr(self, "menu_actions", None)
        if not menu_actions:  # a toolbar toggle during construction, before the menus exist
            return
        has = len(self.catalog) > 0
        in_review = self.stack.currentIndex() == 1
        for a in act.ACTIONS:
            qa = menu_actions[a.id]
            qa.setVisible(not a.flag or features.enabled(a.flag))
            btn = None
            if a.state.startswith("next:"):
                btn = self.next_buttons.get(a.state[5:])
            elif a.state:
                btn = self.buttons.get(a.state)
            ok = (has or not a.needs_picture) and not (a.needs_viewer and in_review)
            qa.setEnabled(ok and (btn is None or btn.isEnabled()))
            if a.checkable and btn is not None:
                qa.blockSignals(True)  # a mirrored state, not a click
                qa.setChecked(btn.isChecked())
                qa.blockSignals(False)
        self._refresh_pill_actions()
        for aid in STAR_ACTIONS:  # no stars with no picture, or under a crop / story edit (which ignores the keys)
            menu_actions[aid].setEnabled(has and self._edit is None)
        for aid, on in (("lights_out", self.lights_out), ("dark_theme", theme.current().dark)):
            qa = menu_actions[aid]  # no button mirrors these two: the window's own state is the truth
            qa.blockSignals(True)
            qa.setChecked(on)
            qa.blockSignals(False)

    def _refresh_pill_actions(self) -> None:
        """Each Pills menu row carries its pill's name and colour dot, is checked when the picture in view has the
        pill, and is hidden while no pill owns its digit."""
        has = len(self.catalog) > 0
        names = {n.casefold() for n in self._current_pill_names()} if has else set()
        dark = theme.current().dark
        for aid, n in act.PILL_DIGITS.items():
            qa = self.menu_actions[aid]
            pill = self.pill_book.by_digit(n)
            qa.setVisible(pill is not None)
            qa.blockSignals(True)  # a mirrored state, not a click
            if pill is None:
                qa.setText(act.by_id()[aid].label)
                qa.setIcon(QIcon())
                qa.setChecked(False)
            else:
                qa.setText(pill.name.replace("&", "&&"))
                pm = QPixmap(14, 14)
                pm.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pm)
                draw_dot(painter, 7, 7, 5, pill.color, dark)
                painter.end()
                qa.setIcon(QIcon(pm))
                qa.setChecked(pill.name.casefold() in names)
            qa.blockSignals(False)
        self.menu_actions["pill_clear"].setEnabled(has and bool(self.pill_book.pills()))

    def act_back(self) -> None:
        self._control(Qt.Key_Left, lambda: self.step(-1))

    def act_next(self) -> None:
        self._control(Qt.Key_Right, lambda: self.step(1))

    def act_mark(self) -> None:
        self._control(Qt.Key_Space, self.toggle_mark)

    def act_advance(self) -> None:
        self.set_advance_on_mark(not self.advance_on_mark)

    def act_zoom(self) -> None:
        self._control(Qt.Key_Z, self.viewer.toggle_zoom)

    def act_zoom_in(self) -> None:
        self.viewer.zoom_in()

    def act_zoom_out(self) -> None:
        self.viewer.zoom_out()

    def act_fullscreen(self) -> None:
        self.set_bleed(not self.is_bleed)

    def act_review(self) -> None:
        self.toggle_review()

    def act_copy(self) -> None:
        self.open_copy()

    def act_prefs(self) -> None:
        self.open_prefs()

    def act_quit(self) -> None:
        self.close()

    def act_mark_all(self) -> None:
        self.mark_all(True)

    def act_unmark_all(self) -> None:
        self.mark_all(False)

    def act_undo_mark_all(self) -> None:
        self.undo_mark_all()

    def act_start_fresh(self) -> None:
        self.start_fresh()

    def act_compare(self) -> None:
        self._compare_button()

    def act_peaking(self) -> None:
        self.toggle_peaking()

    def act_afpoint(self) -> None:
        self.toggle_marker()

    def act_rotate_left(self) -> None:
        self.rotate(-1)

    def act_rotate_right(self) -> None:
        self.rotate(1)

    def act_crop(self) -> None:
        self.start_crop()

    def act_story(self) -> None:
        self.start_story()

    def act_remove(self) -> None:
        self.remove_derived()

    def act_pill_1(self) -> None:
        self._pill_key(1)

    def act_pill_2(self) -> None:
        self._pill_key(2)

    def act_pill_3(self) -> None:
        self._pill_key(3)

    def act_pill_4(self) -> None:
        self._pill_key(4)

    def act_pill_5(self) -> None:
        self._pill_key(5)

    def act_pill_6(self) -> None:
        self._pill_key(6)

    def act_pill_7(self) -> None:
        self._pill_key(7)

    def act_pill_8(self) -> None:
        self._pill_key(8)

    def act_pill_9(self) -> None:
        self._pill_key(9)

    def act_pill_clear(self) -> None:
        self._pill_key(0)

    def act_stars_1(self) -> None:
        self._stars_key(1)

    def act_stars_2(self) -> None:
        self._stars_key(2)

    def act_stars_3(self) -> None:
        self._stars_key(3)

    def act_stars_4(self) -> None:
        self._stars_key(4)

    def act_stars_5(self) -> None:
        self._stars_key(5)

    def act_stars_clear(self) -> None:
        self._stars_key(0)

    def act_pills_manage(self) -> None:
        self.open_pill_manager()

    def act_help(self) -> None:
        self.toggle_shortcuts()

    def act_lights_out(self) -> None:
        self.set_lights_out(not self.lights_out)

    def act_dark_theme(self) -> None:
        """Flip between the dark and light theme and save the pick (theme.choose); Ctrl+T, not T, so a stray key
        while culling cannot change a saved preference."""
        theme.choose(QApplication.instance(), self.prefs, not theme.current().dark, self.retheme)
        self.refresh_actions()

    # -- the `?` overlay
    def toggle_shortcuts(self) -> None:
        ov = self._shortcuts
        if ov is not None and ov.isVisible():
            ov.close_overlay()
            return
        if ov is not None:
            ov.deleteLater()
        from spuza.ui.shortcut_overlay import ShortcutOverlay  # built fresh each time: it reads the theme and the mode

        self._shortcuts = ov = ShortcutOverlay(self)  # over the whole window, bars and sidebar too
        ov.setGeometry(self.rect())
        ov.show()
        ov.raise_()
        ov.setFocus()

    def _overlay_closed(self) -> None:
        if self.stack.currentIndex() == 1:
            self._focus_review()
        else:
            self.setFocus()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if self._shortcuts is not None and self._shortcuts.isVisible():
            self._shortcuts.setGeometry(self.rect())
        self._place_toast()

    # -- keys, theme, close -----------------------------------------------------------------------
    def keyPressEvent(self, e) -> None:
        if e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                            | Qt.KeyboardModifier.MetaModifier):
            return super().keyPressEvent(e)
        k = e.key()
        if self._compare_shown():  # compare owns the keys (plus F, C, P, O, which are global)
            if self._forwarding:
                return  # compare ignored it: never fall through to the hidden viewer
            if k in COMPARE_KEYS:
                self._forward_to_compare(e)
                e.accept()
                return
            if k not in (Qt.Key_F, Qt.Key_C, Qt.Key_P, Qt.Key_O, Qt.Key_Question):
                return super().keyPressEvent(e)
        if self.stack.currentIndex() == 1:
            if k in GRID_KEYS and not self._forwarding:
                self.forward_to_review(e)  # the grid owns these; the hidden viewer never sees them
                e.accept()
                return
            if k in GRID_KEYS and k != Qt.Key_Escape or k in (Qt.Key_J, Qt.Key_K):
                return  # the grid ignored it (or a fake review did): never drive the hidden viewer
        if self._edit is not None and self.stack.currentIndex() == 0 and self._edit_key(e):
            e.accept()
            return
        if Qt.Key_0 <= k <= Qt.Key_9 and self._edit is None:  # 1-9 toggle a pill, 0 clears (crop / story keep their digits)
            if not e.isAutoRepeat():  # a held digit must not flip a pill back and forth
                self._pill_key(k - Qt.Key_0)
        elif k in (Qt.Key_Space, Qt.Key_M):
            if not e.isAutoRepeat():  # a held key marks one picture, never a run (move on mark would)
                self.toggle_mark()
        elif k in (Qt.Key_Right, Qt.Key_K):
            self.step(1)
        elif k in (Qt.Key_Left, Qt.Key_J):
            self.step(-1)
        elif k == Qt.Key_Z:
            self.viewer.toggle_zoom()
        elif k in (Qt.Key_Plus, Qt.Key_Equal):  # + (Shift+= on US keyboards, or the keypad), and =
            self.viewer.zoom_in()
        elif k == Qt.Key_Minus:
            self.viewer.zoom_out()
        elif k == Qt.Key_F:
            self.set_bleed(not self.is_bleed)
        elif k == Qt.Key_R:
            self.toggle_review()
        elif k == Qt.Key_C:
            self.open_copy()
        elif k == Qt.Key_N and features.enabled("compare_view"):
            if not e.isAutoRepeat():
                self.open_compare()
        elif k == Qt.Key_P and features.enabled("focus_peaking"):
            if not e.isAutoRepeat():
                self.toggle_peaking()
        elif k == Qt.Key_O and features.enabled("focus_point"):
            if not e.isAutoRepeat():
                self.toggle_marker()
        elif k == Qt.Key_X and self.stack.currentIndex() == 0:
            if not e.isAutoRepeat():
                self.start_crop()
        elif k == Qt.Key_S and self.stack.currentIndex() == 0:
            if not e.isAutoRepeat():
                self.start_story()
        elif k == Qt.Key_Backspace and self.stack.currentIndex() == 0:
            if not e.isAutoRepeat():
                self.remove_derived()
        elif k == Qt.Key_U and features.enabled("resume_session") and self.stack.currentIndex() == 0:
            if not e.isAutoRepeat():
                self.start_fresh()
        elif k == Qt.Key_Question:
            if not e.isAutoRepeat():
                self.toggle_shortcuts()
        elif k == Qt.Key_Escape:
            if self.stack.currentIndex() == 1:
                self.close_review()
            elif self.is_bleed:
                self.set_bleed(False)
        else:
            return super().keyPressEvent(e)
        e.accept()

    def retheme(self) -> None:
        c = theme.current()
        self.quiet_bar.retheme()  # also re-tints the icons
        self.pill_tray.update()
        self.pill.retheme()
        self._toast.setStyleSheet(f"background: {c.hud}; color: {tk.DARK['text']}; border-radius: {tk.RADIUS['sheet']}px;"
                                  " padding: 6px 14px;")
        self.advanced_tag.setStyleSheet(f"font-weight: bold; padding: 0 8px; color: {c.text_dim};")
        if self._compare_strip is not None:
            self._style_compare_strip(self._compare_strip)
        if self.compare is not None:
            self.compare.retheme()
        self.sidebar.retheme()
        self.viewer.retheme()
        self.filmstrip.retheme()
        self.inspector.retheme()
        self.ratio_strip.retheme()
        self.viewer.strip.set_state(self.catalog.position_text(), bool(self.catalog.current) and
                                    self.catalog.is_marked(self.catalog.index),
                                    self.catalog.current.stem if self.catalog.current else "",
                                    self.catalog.current.kind if self.catalog.current else "",
                                    self._marked_total())
        self.refresh_actions()  # the Dark theme check follows the theme, also after an OS follow on macOS

    def closeEvent(self, e) -> None:
        self._pill_cancel.set()
        self._pill_gen += 1  # a late result is ignored
        self._pill_read_pool.waitForDone(PILL_JOIN_MS)
        if self._pill_mgr is not None:
            self._pill_mgr.reject()
        self._save_pills_now()
        if self.resume is not None:
            self.resume.flush()  # first: a bounded (~1 s) write of the marks and place
        if self._prune_thread is not None:
            self._prune_thread.join(2.0)
        if self.compare is not None:
            self.compare.shutdown()
            self._cmp_gen += 1
            self._cmp_pool.clear()
            self._cmp_pool.waitForDone(MASK_WAIT_MS)
        if self.peaking is not None:
            self.peaking.shutdown()
        self.filmstrip.shutdown()
        self.prefetcher.shutdown()
        self.viewer.shutdown()
        super().closeEvent(e)
