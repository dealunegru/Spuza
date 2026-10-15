"""Colours that follow the system dark / light setting (Qt 6.5+ QStyleHints.colorScheme)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette


@dataclass(frozen=True)
class Colors:
    dark: bool
    window: str
    panel: str
    base: str
    text: str
    dim: str
    border: str
    accent: str  # selection, current frame, lit buttons
    green: str  # marked, decoded-ahead band
    amber: str  # unpaired raw
    stage: str  # behind the picture


DARK = Colors(True, "#262628", "#2d2d30", "#1e1e1e", "#e8e8ea", "#9a9aa2", "#3d3d42",
              "#4c8dff", "#30d158", "#ffb340", "#101012")
LIGHT = Colors(False, "#ececee", "#f5f5f7", "#ffffff", "#1d1d1f", "#6e6e73", "#d0d0d5",
               "#0a6cff", "#1f9d40", "#b36b00", "#d9d9dc")

_current = DARK


def current() -> Colors:
    return _current


def is_dark(app: QGuiApplication | None = None) -> bool:
    app = app or QGuiApplication.instance()
    if app is None:
        return True
    return app.styleHints().colorScheme() != Qt.ColorScheme.Light


def apply(app: QGuiApplication, dark: bool) -> Colors:
    """Install the palette for `dark` (Fusion style honours it on every platform)."""
    global _current
    _current = DARK if dark else LIGHT
    c = _current
    app.setStyle("Fusion")
    p = QPalette()
    for role, col in (
        (QPalette.Window, c.window), (QPalette.WindowText, c.text), (QPalette.Base, c.base),
        (QPalette.AlternateBase, c.panel), (QPalette.Text, c.text), (QPalette.Button, c.panel),
        (QPalette.ButtonText, c.text), (QPalette.Highlight, c.accent), (QPalette.HighlightedText, "#ffffff"),
        (QPalette.ToolTipBase, c.panel), (QPalette.ToolTipText, c.text), (QPalette.PlaceholderText, c.dim),
        (QPalette.Mid, c.border), (QPalette.Dark, c.border),
    ):
        p.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c.dim))
    app.setPalette(p)
    return c


def follow(app: QGuiApplication, on_change=None) -> None:
    """Apply the system scheme now and again whenever it changes."""
    def update(*_):
        apply(app, is_dark(app))
        if on_change:
            on_change()
    update()
    app.styleHints().colorSchemeChanged.connect(update)
