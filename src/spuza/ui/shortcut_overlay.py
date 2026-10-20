"""The `?` overlay: every action grouped by task, drawn over the photo. Built from ui/actions.py, so it matches the menus."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from spuza import features
from spuza.ui import actions as act
from spuza.ui import theme


def rows() -> list[tuple[str, str, str]]:
    """(group, label, keys as this platform writes them) for every action, then the mode-only keys."""
    out = [(a.group, a.label, act.native_keys(a)) for a in act.ACTIONS]
    out += [(g, what, keys) for g, keys, what in act.CONTEXT_KEYS]
    return out


class ShortcutOverlay(QFrame):
    """Covers its parent. Esc, `?` or a click closes it; every other key is swallowed, never reaching the picture."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("shortcut_overlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(False)
        c = theme.current()
        veil = QColor(c.window)
        self.setStyleSheet(
            f"#shortcut_overlay {{ background: rgba({veil.red()},{veil.green()},{veil.blue()},238); }}"
            f"#shortcut_overlay QLabel {{ color: {c.text}; background: transparent; }}"
            f"#shortcut_overlay QLabel[role='dim'] {{ color: {c.dim}; }}"
            f"#shortcut_overlay QLabel[role='head'] {{ color: {c.accent}; font-weight: 600; padding-top: 8px; }}"
            f"#shortcut_overlay QLabel[role='key'] {{ color: {c.text}; background: {c.panel};"
            f" border: 1px solid {c.border}; border-radius: 4px; padding: 1px 6px; }}"
            "")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 30, 40, 30)
        title = QLabel("Keyboard shortcuts")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        outer.addWidget(title)
        hint = QLabel("Esc or ? closes this.  Greyed rows need a mode that is off.")
        hint.setProperty("role", "dim")
        outer.addWidget(hint)
        cols = QHBoxLayout()
        outer.addLayout(cols, 1)
        by_group: dict[str, list[tuple[str, str, bool]]] = {g: [] for g in act.GROUPS}
        for a in act.ACTIONS:
            by_group[a.group].append((a.label, act.native_keys(a), not a.flag or features.enabled(a.flag)))
        for g, keys, what in act.CONTEXT_KEYS:
            by_group[g].append((what, keys, True))
        column = None
        for n, (g, items) in enumerate(g_ for g_ in by_group.items() if g_[1]):
            if column is None or n % 3 == 0:
                column = QVBoxLayout()
                column.setAlignment(Qt.AlignmentFlag.AlignTop)
                cols.addLayout(column, 1)
            head = QLabel(g.upper())
            head.setProperty("role", "head")
            column.addWidget(head)
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            for r, (label, keys, live) in enumerate(items):
                name, key = QLabel(label), QLabel(keys)
                key.setProperty("role", "key")
                if not live:
                    name.setProperty("role", "dim")
                grid.addWidget(name, r, 0)
                grid.addWidget(key, r, 1, Qt.AlignmentFlag.AlignLeft)
            column.addLayout(grid)
        self.hide()

    def labels(self) -> list[str]:
        return [w.text() for w in self.findChildren(QLabel)]

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Escape, Qt.Key_Question):
            if not e.isAutoRepeat():
                self.close_overlay()
        e.accept()

    def mousePressEvent(self, e) -> None:
        self.close_overlay()
        e.accept()

    def close_overlay(self) -> None:
        self.hide()
        w = self.window()
        if hasattr(w, "_overlay_closed"):
            w._overlay_closed()
