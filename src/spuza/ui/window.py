"""The main window: toolbar, sidebar | viewer | inspector, filmstrip, status bar, review page, full-bleed.

Design: 02-bench, with 01-bleed as its full-screen mode. Every key has a visible toolbar button.
The review grid and the copy sheet are built lazily through `review_factory` / `copy_sheet_factory`
so they are only imported when used (and tests can inject fakes).
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton, QSizePolicy, QSplitter,
                               QStackedWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from spuza.copy import human_size
from spuza.ui import theme
from spuza.ui.filmstrip import Filmstrip
from spuza.ui.inspector import Inspector
from spuza.ui.viewer import Viewer

DOT = "·"
GRID_KEYS = frozenset({Qt.Key_Space, Qt.Key_M, Qt.Key_Right, Qt.Key_Left, Qt.Key_Up, Qt.Key_Down,
                       Qt.Key_Return, Qt.Key_Enter, Qt.Key_Z, Qt.Key_Escape})
# (name, label, key cap shown on the button, checkable)
BUTTONS = [
    ("back", "Back", "←/K", False),
    ("next", "Next", "→/J", False),
    ("mark", "Mark", "Space/M", True),
    ("zoom", "Zoom 100%", "Z", True),
    ("fullscreen", "Full screen", "F", True),
    ("review", "Review", "R", True),
    ("copy", "Copy…", "C", False),
]


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

    def __init__(self, catalog, prefetcher, root, clock=time.monotonic, parent=None):
        super().__init__(parent)
        self.catalog, self.prefetcher, self.root = catalog, prefetcher, Path(root)
        self.clock, self._t_open = clock, clock()
        self.review_factory = None
        self.copy_sheet_factory = None
        self.review = None
        self._sheet = None
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
        for name, label, cap, checkable in BUTTONS:
            if name in ("mark", "review"):
                self.toolbar.addSeparator()
            if name == "copy":
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
        b["zoom"].clicked.connect(lambda: self._control(Qt.Key_Z, self.viewer.toggle_zoom))
        b["fullscreen"].clicked.connect(lambda: self.set_bleed(not self.is_bleed))
        b["review"].clicked.connect(lambda: self.toggle_review())
        b["copy"].clicked.connect(lambda: self.open_copy())

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
        self.lbl_ready, self.lbl_elapsed = QLabel(), QLabel()
        sb.addWidget(self.lbl_pos)
        sb.addWidget(self.lbl_marked)
        sb.addPermanentWidget(self.lbl_ready)
        sb.addPermanentWidget(self.lbl_elapsed)

        prefetcher.ready.connect(self._on_ready)
        prefetcher.failed.connect(self._on_failed)
        self.viewer.shown.connect(self._on_shown)
        self.viewer.zoom_changed.connect(lambda z: self.buttons["zoom"].setChecked(z))
        self.filmstrip.picked.connect(self.go_to)
        self.sidebar.show_all.connect(self.close_review)
        self.sidebar.show_marked.connect(self.open_review)
        QShortcut(QKeySequence(QKeySequence.StandardKey.FullScreen), self,
                  activated=lambda: self.set_bleed(not self.is_bleed))

        self._tick = QTimer(self)  # elapsed clock; repeating, stopped in closeEvent
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self.refresh_status)
        self._tick.start()

        self.retheme()
        prefetcher.update(catalog.index)
        self._render_current()

    # -- navigation -------------------------------------------------------------------------------
    def step(self, delta: int) -> None:
        if (self.catalog.next() if delta > 0 else self.catalog.back()):
            self._moved()

    def go_to(self, i: int) -> None:
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
        self.catalog.toggle_mark(i)
        self.filmstrip.item_changed(i)
        self.refresh_marks()

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
        self.filmstrip.set_current(i)
        self.refresh_marks()

    def refresh_marks(self) -> None:
        """Everything that depends on the current picture or the marked set."""
        cat, i = self.catalog, self.catalog.index
        pic = cat.current
        marked = bool(pic) and cat.is_marked(i)
        self.buttons["mark"].setChecked(marked)
        self.sidebar.set_counts(len(cat), cat.marked_count)
        if pic is not None:
            self.viewer.strip.set_state(cat.position_text(), marked, pic.stem, pic.kind, cat.marked_count)
            self.inspector.set_picture(pic, self.prefetcher.info(i), marked, self.prefetcher.failed_reason(i))
        else:
            self.inspector.set_picture(None)
        self.refresh_status()

    def refresh_status(self) -> None:
        cat = self.catalog
        secs = max(0, int(self.clock() - self._t_open))
        self.lbl_pos.setText(cat.position_text())
        self.lbl_marked.setText(f"{cat.marked_count} marked {DOT} {human_size(cat.marked_bytes)}")
        self.lbl_ready.setText(f"next {self.prefetcher.ready_ahead()} ready")
        self.lbl_elapsed.setText(f"{secs // 60:02d}:{secs % 60:02d}")

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
        self.viewer.strip.setVisible(on)
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
        if self.stack.currentIndex() == 1:
            self.close_review()
        else:
            self.open_review()

    def _default_review_factory(self, catalog, prefetcher=None, parent=None):
        from spuza.ui.review import ReviewGrid  # lazy: written beside this module, imported on first use

        return ReviewGrid(catalog, prefetcher, parent)

    def open_review(self) -> None:
        if self.review is None:
            factory = self.review_factory or self._default_review_factory
            try:
                self.review = factory(self.catalog, prefetcher=self.prefetcher, parent=self)
            except Exception as exc:  # noqa: BLE001 - e.g. the module is missing; stay on the viewer
                self.say(f"Review is not available: {exc}")
                return
            self.stack.addWidget(self.review)
            self.review.open_index.connect(self._review_open_index)
            self.review.marks_changed.connect(self._review_marks_changed)
            self.review.closed.connect(self.close_review)
        self.review.refresh()
        self.stack.setCurrentIndex(1)
        self.buttons["review"].setChecked(True)
        self.buttons["fullscreen"].setEnabled(False)  # full-bleed is inert in review: show it
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

    def _select_in_review(self, i: int) -> None:
        """A picture was picked while review is shown: select it in the grid if it is marked, else ignore."""
        r = self.review
        row = r._row_of(i) if hasattr(r, "_row_of") else -1
        if row >= 0:
            r.view.setCurrentIndex(r.model.index(row, 0))
        self._focus_review()

    def _control(self, key, viewer_action) -> None:
        """A toolbar control: in review it does what the same key does on the grid, else drives the viewer."""
        if self.stack.currentIndex() == 1:
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
            self._sheet = factory(marked, default_dest=self.root / "Spuza", parent=self)
            (getattr(self._sheet, "open", None) or self._sheet.show)()
        except Exception as exc:  # noqa: BLE001
            self.say(f"Copy is not available: {exc}")

    # -- keys, theme, close -----------------------------------------------------------------------
    def keyPressEvent(self, e) -> None:
        if e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                            | Qt.KeyboardModifier.MetaModifier):
            return super().keyPressEvent(e)
        k = e.key()
        if self.stack.currentIndex() == 1:
            if k in GRID_KEYS and not self._forwarding:
                self.forward_to_review(e)  # the grid owns these; the hidden viewer never sees them
                e.accept()
                return
            if k in GRID_KEYS and k != Qt.Key_Escape or k in (Qt.Key_J, Qt.Key_K):
                return  # the grid ignored it (or a fake review did): never drive the hidden viewer
        if k in (Qt.Key_Space, Qt.Key_M):
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
        self.toolbar.setStyleSheet(
            f"QToolBar {{ spacing: 6px; padding: 4px 8px; border: 0; border-bottom: 1px solid {c.border}; }}"
            f"QToolButton {{ padding: 6px 12px; border: 1px solid {c.border}; border-radius: 6px;"
            f" background: {c.panel}; }}"
            f"QToolButton:hover {{ border-color: {c.accent}; }}"
            f"QToolButton:checked {{ background: {c.accent}; color: #ffffff; border-color: {c.accent}; }}"
            f"#btn_copy {{ background: {c.accent}; color: #ffffff; font-weight: 600; border-color: {c.accent}; }}")
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
        self._tick.stop()
        self.prefetcher.shutdown()
        self.viewer.shutdown()
        super().closeEvent(e)
