"""The main window: toolbar, sidebar | viewer | inspector, filmstrip, status bar, review page, full-bleed.

Design: 02-bench, with 01-bleed as its full-screen mode. Every key has a visible toolbar button.
The review grid and the copy sheet are built lazily through `review_factory` / `copy_sheet_factory`
so they are only imported when used (and tests can inject fakes).

Advanced mode wires four more features, each behind its flag and each a no-op (key falls through, widget
hidden, nothing written) in classic: N compare (compare_view), P peaking (focus_peaking), O AF point and Z
landing on it (focus_point), and resume (resume_session; U starts fresh). Their objects are built on first use.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton, QSizePolicy, QSplitter,
                               QStackedWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from spuza import features
from spuza.copy import human_size
from spuza.ui import settings, theme
from spuza.ui.filmstrip import Filmstrip
from spuza.ui.inspector import Inspector
from spuza.ui.viewer import Viewer

DOT = "·"


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


def default_copy_dest() -> Path:
    """Where Copy… starts: a folder on the drive, never the card being culled (tests patch Path.home)."""
    return Path.home() / "Pictures" / "Spuza"


_NOT_IN_REVIEW = object()
GRID_KEYS = frozenset({Qt.Key_Space, Qt.Key_M, Qt.Key_Right, Qt.Key_Left, Qt.Key_Up, Qt.Key_Down,
                       Qt.Key_Return, Qt.Key_Enter, Qt.Key_Z, Qt.Key_Escape})
# (name, label, key cap shown on the button, checkable)
PREFS_CAP = "⌘," if sys.platform == "darwin" else "Ctrl+,"
BUTTONS = [
    ("back", "Back", "←/K", False),
    ("next", "Next", "→/J", False),
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
COMPARE_KEYS = frozenset({Qt.Key_Left, Qt.Key_Right, Qt.Key_J, Qt.Key_K, Qt.Key_Space, Qt.Key_M, Qt.Key_Z,
                          Qt.Key_N, Qt.Key_Escape})
MASK_WAIT_MS = 3000  # the longest closeEvent waits for compare's mask workers


class KeyButton(QToolButton):
    def __init__(self, name: str, label: str, cap: str, checkable: bool, parent=None):
        super().__init__(parent)
        self.name, self.label, self.cap = name, label, cap
        self.setText(f"{label}   {cap}")
        self.setCheckable(checkable)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(f"{label} ({cap})")
        self.setObjectName(f"btn_{name}")


class Sidebar(QWidget):
    """Source root and counts; 'All' is always above 'Marked'."""

    show_all = Signal()
    show_marked = Signal()

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
            btn.clicked.connect(lambda _=False, s=sig: s.emit())
            cnt = QLabel("0")
            row.addWidget(btn, 1)
            row.addWidget(cnt)
            lay.addLayout(row)
            self._rows.append((name, btn, cnt))
        lay.addStretch(1)
        self.set_root(root)

    def set_root(self, root: Path) -> None:
        self._root = str(root)
        self.root_label.setText(self._root)
        self.root_label.setToolTip(self._root)

    def root_text(self) -> str:
        return self._root

    def set_counts(self, total: int, marked: int) -> None:
        for (name, _b, cnt), n in zip(self._rows, (total, marked)):
            cnt.setText(str(n))

    def row_names(self) -> list[str]:
        return [n for n, _b, _c in self._rows]

    def counts(self) -> dict[str, int]:
        return {n: int(c.text()) for n, _b, c in self._rows}

    def retheme(self) -> None:
        c = theme.current()
        self.head.setStyleSheet(f"color: {c.dim}; font-size: 11px; font-weight: 600;")
        for _n, btn, cnt in self._rows:
            btn.setStyleSheet(f"text-align: left; padding: 4px 6px; color: {c.text}; border: 0;")
            cnt.setStyleSheet(f"color: {c.dim};")


class MainWindow(QMainWindow):
    step_timed = Signal(int, float)  # picture index, ms from key handled to its pixmap set

    def __init__(self, catalog, prefetcher, root, clock=time.monotonic, parent=None, prefs=None, resume=True):
        super().__init__(parent)
        self._resume_allowed = bool(resume)  # False for --bench: a scripted run neither reads nor writes sessions
        self.catalog, self.prefetcher, self.root = catalog, prefetcher, Path(root)
        self.prefs = prefs if prefs is not None else settings.open_settings()
        self.advance_on_mark = settings.read_bool(self.prefs, settings.ADVANCE_ON_MARK, False)
        self.clock = clock  # kept for callers that pass it; the status strip no longer uses it
        self.review_factory = None
        self.copy_sheet_factory = None
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
        self._pruned = False
        self._prune_thread = None
        self._sheet = None
        self._prefs_sheet = None
        self._prefs_seed = False
        self.last_message = ""
        self.is_bleed = False
        self._forwarding = False
        self._was_maximized = False
        self._t_key: float | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # can hold focus, so close_review can restore it
        self.setWindowTitle(f"Spuza — {self.root}")
        self.resize(1280, 800)

        self.viewer = Viewer()
        self.sidebar = Sidebar(self.root)
        self.inspector = Inspector()
        self.filmstrip = Filmstrip(catalog, prefetcher)

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
        for name, label, cap, checkable in BUTTONS:
            if name in ("mark", "review"):
                self.toolbar.addSeparator()
            if name == "copy":
                # Preferences sits left of the copy spacer; it is not in `buttons` (those are the key-driven
                # picture controls, and review mode keeps all of them in step)
                self.prefs_button = KeyButton("prefs", "Preferences…", PREFS_CAP, False)
                self.toolbar.addSeparator()
                self.toolbar.addWidget(self.prefs_button)
                spacer = QWidget()
                spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                self.toolbar.addWidget(spacer)
            b = KeyButton(name, label, cap, checkable)
            self.buttons[name] = b
            self.toolbar.addWidget(b)
        b = self.buttons
        b["back"].clicked.connect(lambda: self._control(Qt.Key_Left, lambda: self.step(-1)))
        b["next"].clicked.connect(lambda: self._control(Qt.Key_Right, lambda: self.step(1)))
        b["mark"].clicked.connect(lambda: self._control(Qt.Key_Space, self.toggle_mark))
        b["advance"].clicked.connect(lambda: self.set_advance_on_mark(not self.advance_on_mark))
        b["zoom"].clicked.connect(lambda: self._control(Qt.Key_Z, self.viewer.toggle_zoom))
        b["fullscreen"].clicked.connect(lambda: self.set_bleed(not self.is_bleed))
        b["review"].clicked.connect(lambda: self.toggle_review())
        b["copy"].clicked.connect(lambda: self.open_copy())
        self.prefs_button.clicked.connect(lambda: self.open_prefs())
        nb = self.next_buttons
        nb["compare"].clicked.connect(lambda: self._compare_button())
        nb["peaking"].clicked.connect(lambda: self.toggle_peaking())
        nb["afpoint"].clicked.connect(lambda: self.toggle_marker())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.sidebar)
        split.addWidget(self.viewer)
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
        self.setCentralWidget(self.stack)

        sb = self.statusBar()
        self.lbl_pos, self.lbl_marked = QLabel(), QLabel()
        sb.addWidget(self.lbl_pos)
        sb.addWidget(self.lbl_marked)
        self.advanced_tag = QLabel("ADVANCED")
        self.advanced_tag.setStyleSheet("font-weight: bold; padding: 0 8px;")
        sb.addPermanentWidget(self.advanced_tag)
        self._show_mode()

        prefetcher.ready.connect(self._on_ready)
        prefetcher.failed.connect(self._on_failed)
        self.viewer.shown.connect(self._on_shown)
        self.viewer.zoom_changed.connect(lambda z: self.buttons["zoom"].setChecked(z))
        self.filmstrip.picked.connect(self.go_to)
        self.sidebar.show_all.connect(self.close_review)
        self.sidebar.show_marked.connect(self.open_review)
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

        self.retheme()
        self.buttons["advance"].setChecked(self.advance_on_mark)
        self.viewer.zoom_target = self._zoom_target  # None unless advanced + focus_point: Z centres as shipped
        if features.enabled("resume_session"):
            # restore BEFORE the first prefetch so the first paint is the restored place, and no mark can be made
            # (and saved over the old session) before it ran
            ctl = self._ensure_resume()
            self._banner = ctl.restore() if ctl is not None else None
        prefetcher.update(catalog.index)
        self._enter_next_features()  # after update(): importing numpy for peaking must not delay the first decodes
        self._render_current()

    # -- navigation -------------------------------------------------------------------------------
    def step(self, delta: int) -> None:
        if (self.catalog.next() if delta > 0 else self.catalog.back()):
            self._moved()

    def go_to(self, i: int) -> None:
        if self._compare_shown():
            return  # the filmstrip is behind compare; a stray pick must not move the viewer under it
        if self.stack.currentIndex() == 1:
            self._select_in_review(i)  # review stays up; the viewer's picture does not move
            return
        if len(self.catalog) and i != self.catalog.index:
            self.catalog.go(i)
            self._moved()
            if self.stack.currentIndex() == 1:
                self._focus_review()

    def _moved(self) -> None:
        self._t_key = time.perf_counter()
        self.prefetcher.update(self.catalog.index)
        self._render_current()

    def toggle_mark(self) -> None:
        if not len(self.catalog):
            return
        i = self.catalog.index
        now_marked = self.catalog.toggle_mark(i)
        self.filmstrip.item_changed(i)
        self.refresh_marks()
        if now_marked and self.advance_on_mark and self.stack.currentIndex() == 0:
            self.step(1)  # "move on mark": a keeper is decided, show the next one (last picture: stays)

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

    # -- rendering --------------------------------------------------------------------------------
    def _render_current(self) -> None:
        i, pic = self.catalog.index, self.catalog.current
        if pic is None:
            self.viewer.show_message(f"No pictures found in {self.root}")
        else:
            img = self.prefetcher.cached(i)
            reason = self.prefetcher.failed_reason(i)
            if img is not None:
                self.viewer.show_image(i, pic, img, self.prefetcher.info(i))
            elif reason:
                self.viewer.show_error(i, pic, reason)
            else:
                self.viewer.show_placeholder(i, pic)
            self._overlays_for(i, img)
            if img is not None or reason:
                self._say_banner()
        self.filmstrip.set_current(i)
        self.refresh_marks()

    def _overlays_for(self, i: int, img) -> None:
        """Peaking mask and AF ring for the picture just shown (both no-ops while off or in classic)."""
        if self.peaking is not None:
            self.peaking.show(i, img)
        if self.marker is not None:
            self.marker.set_point(self._focus_frac(i) if img is not None else None)

    def _say_banner(self) -> None:
        if self._banner:
            banner, self._banner = self._banner, None
            self.say(banner)

    def refresh_marks(self) -> None:
        """Everything that depends on the current picture or the marked set."""
        cat, i = self.catalog, self.catalog.index
        pic = cat.current
        marked = bool(pic) and cat.is_marked(i)
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
        self.sidebar.set_counts(len(cat), cat.marked_count)
        if pic is not None:
            self.viewer.strip.set_state(cat.position_text(), marked, pic.stem, pic.kind, cat.marked_count)
            self.inspector.set_picture(pic, self.prefetcher.info(i), marked, self.prefetcher.failed_reason(i))
        else:
            self.inspector.set_picture(None)
        self.refresh_status()

    def refresh_status(self) -> None:
        cat = self.catalog
        self.lbl_pos.setText(cat.position_text())
        self.lbl_marked.setText(f"{cat.marked_count} marked {DOT} {human_size(cat.marked_bytes)}")

    def _on_ready(self, i: int, _img, _info) -> None:
        self.filmstrip.item_changed(i)
        if i == self.catalog.index:
            self._render_current()
        else:
            self.refresh_status()

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
        else:
            self.statusBar().showMessage(text, 5000)

    # -- modes ------------------------------------------------------------------------------------
    def set_bleed(self, on: bool) -> None:
        if on == self.is_bleed or (on and self.stack.currentIndex() == 1):
            self.buttons["fullscreen"].setChecked(self.is_bleed)  # full-bleed is inert in review
            return
        self.is_bleed = on
        for w in (self.toolbar, self.sidebar, self.inspector, self.filmstrip, self.statusBar()):
            w.setVisible(not on)
        self.toolbar2.setVisible(not on and self._next_row_wanted())
        self.viewer.strip.setVisible(on)
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

    def toggle_review(self) -> None:
        if self._compare_shown():
            return  # leave compare first (Esc); the Review button is disabled meanwhile
        if self.stack.currentIndex() == 1:
            self.close_review()
        else:
            self.open_review()

    def _default_review_factory(self, catalog, prefetcher=None, parent=None):
        from spuza.ui.review import ReviewGrid  # lazy: written beside this module, imported on first use

        return ReviewGrid(catalog, prefetcher, parent)

    def open_review(self) -> None:
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
            self.stack.insertWidget(1, self.review)
            self.review.open_index.connect(self._review_open_index)
            self.review.marks_changed.connect(self._review_marks_changed)
            self.review.closed.connect(self.close_review)
        self.review.refresh()
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
        self.buttons["review"].setChecked(False)
        self.buttons["fullscreen"].setEnabled(True)
        self.refresh_marks()
        self.setFocus()

    def _review_open_index(self, i: int) -> None:
        self.catalog.go(i)
        self.close_review()
        self._moved()

    def _review_marks_changed(self) -> None:
        self.filmstrip.viewport().update()
        self.refresh_marks()

    def _default_copy_sheet_factory(self, pictures, default_dest=None, parent=None):
        from spuza.ui.copy_sheet import CopySheet  # lazy, see above

        return CopySheet(pictures, default_dest=default_dest, parent=parent)

    def open_copy(self) -> None:
        marked = self.catalog.marked()
        if not marked:
            self.say("Nothing is marked yet: press Space on the pictures you want to copy.")
            return
        factory = self.copy_sheet_factory or self._default_copy_sheet_factory
        try:
            self._sheet = factory(marked, default_dest=default_copy_dest(), parent=self)
            (getattr(self._sheet, "open", None) or self._sheet.show)()
        except Exception as exc:  # noqa: BLE001
            self.say(f"Copy is not available: {exc}")

    def _default_prefs_sheet_factory(self, ahead, behind, max_side, parent=None):
        from spuza.ui.prefs_sheet import PrefsSheet  # lazy, like the copy sheet

        return PrefsSheet(ahead, behind, max_side, parent=parent, advanced=features.is_advanced(),
                          threads=settings.read_threads(self.prefs),
                          peaking_threshold=settings.read_peaking_threshold(self.prefs),
                          resume=settings.read_resume_on(self.prefs))

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
            if ctl is not None and self.catalog.marked_count == 0 and self.catalog.index == 0:
                self._banner = ctl.restore()
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
        self.prefs.sync()
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
        if i is None or i < 0 or not features.enabled("focus_point"):
            return None
        try:
            fi = getattr(self.prefetcher.info(i), "focus", None)
            return fi.frac() if fi is not None else None
        except Exception:  # noqa: BLE001 - a bad point must never break Z or a step
            return None

    def _zoom_target(self):
        return self._focus_frac(self.viewer.index)

    def _ensure_peaking(self):
        if self.peaking is None:
            from spuza.ui.peaking_overlay import PeakingOverlay  # lazy: numpy stays out of classic start-up

            self.peaking = PeakingOverlay(self.viewer, threshold=settings.read_peaking_threshold(self.prefs))
        return self.peaking

    def _ensure_marker(self):
        if self.marker is None:
            from spuza.ui.focus_marker import FocusMarker

            self.marker = FocusMarker(self.viewer)
        return self.marker

    def _enter_next_features(self) -> None:
        """Start-up, or a live switch to advanced: restore the saved peaking / ring states, show the buttons."""
        if features.enabled("focus_peaking"):
            on = settings.read_peaking_on(self.prefs)
            if on or self.peaking is not None:  # built only when wanted: numpy stays unimported while P is off
                self._ensure_peaking().set_on(on)
        if features.enabled("focus_point"):
            on = settings.read_focus_marker(self.prefs)
            if on or self.marker is not None:
                self._ensure_marker().set_on(on)
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

    def _next_row_wanted(self) -> bool:
        return any(features.enabled(flag) for _n, _l, _c, flag in NEXT_BUTTONS)

    def _sync_next_buttons(self) -> None:
        for name, _label, _cap, flag in NEXT_BUTTONS:
            self._next_actions[name].setVisible(features.enabled(flag))
        self.toolbar2.setVisible(self._next_row_wanted() and not self.is_bleed)
        nb = self.next_buttons
        nb["compare"].setChecked(self._compare_shown())
        nb["peaking"].setChecked(self.peaking is not None and self.peaking.is_on())
        nb["afpoint"].setChecked(self.marker is not None and self.marker.is_on())

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
            ov.show(self.catalog.index, self.prefetcher.cached(self.catalog.index))
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
            self.resume = ResumeController(self.catalog, self.root, store, parent=self)
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
        if ctl is None:
            return
        text = ctl.start_fresh()
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
            strip = QLabel()
            strip.setStyleSheet("background: rgba(0,0,0,150); color: #f2f2f4; padding: 6px 14px;")
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

    def _review_picks(self) -> list[int]:
        fn = getattr(self.review, "selected_indices", None)
        try:
            return list(fn()) if callable(fn) else []
        except Exception:  # noqa: BLE001 - a fake review
            return []

    def open_compare(self) -> None:
        if not features.enabled("compare_view") or not len(self.catalog) or self._compare_shown():
            return
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
            if k not in (Qt.Key_F, Qt.Key_C, Qt.Key_P, Qt.Key_O):
                return super().keyPressEvent(e)
        if self.stack.currentIndex() == 1:
            if k in GRID_KEYS and not self._forwarding:
                self.forward_to_review(e)  # the grid owns these; the hidden viewer never sees them
                e.accept()
                return
            if k in GRID_KEYS and k != Qt.Key_Escape or k in (Qt.Key_J, Qt.Key_K):
                return  # the grid ignored it (or a fake review did): never drive the hidden viewer
        if k in (Qt.Key_Space, Qt.Key_M):
            if not e.isAutoRepeat():  # a held key marks one picture, never a run (move on mark would)
                self.toggle_mark()
        elif k in (Qt.Key_Right, Qt.Key_J):
            self.step(1)
        elif k in (Qt.Key_Left, Qt.Key_K):
            self.step(-1)
        elif k == Qt.Key_Z:
            self.viewer.toggle_zoom()
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
        elif k == Qt.Key_U and features.enabled("resume_session") and self.stack.currentIndex() == 0:
            if not e.isAutoRepeat():
                self.start_fresh()
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
        bar_css = (
            f"QToolBar {{ spacing: 6px; padding: 4px 8px; border: 0; border-bottom: 1px solid {c.border}; }}"
            f"QToolButton {{ padding: 6px 12px; border: 1px solid {c.border}; border-radius: 6px;"
            f" background: {c.panel}; }}"
            f"QToolButton:hover {{ border-color: {c.accent}; }}"
            f"QToolButton:checked {{ background: {c.accent}; color: #ffffff; border-color: {c.accent}; }}"
            f"#btn_copy {{ background: {c.accent}; color: #ffffff; font-weight: 600; border-color: {c.accent}; }}")
        self.toolbar.setStyleSheet(bar_css)
        self.toolbar2.setStyleSheet(bar_css)
        self.sidebar.retheme()
        self.viewer.retheme()
        self.filmstrip.retheme()
        self.inspector.retheme()
        self.viewer.strip.set_state(self.catalog.position_text(), bool(self.catalog.current) and
                                    self.catalog.is_marked(self.catalog.index),
                                    self.catalog.current.stem if self.catalog.current else "",
                                    self.catalog.current.kind if self.catalog.current else "",
                                    self.catalog.marked_count)

    def follow_system_theme(self, app) -> None:
        theme.follow(app, self.retheme)

    def closeEvent(self, e) -> None:
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
        self.prefetcher.shutdown()
        self.viewer.shutdown()
        super().closeEvent(e)
