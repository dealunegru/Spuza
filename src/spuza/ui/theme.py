"""The theme engine: design tokens (ui/tokens.py) -> Colors, a QPalette and one app-wide stylesheet.

Dark is the default. On macOS Spuza follows the system appearance until the user picks a theme; the
pick is saved (settings.THEME) and from then on wins. Windows and Linux start dark.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette

from spuza.ui import settings
from spuza.ui import tokens as tk


@dataclass(frozen=True)
class Colors:
    dark: bool
    # the names callers used before the tokens
    window: str
    panel: str
    base: str
    text: str
    dim: str
    border: str
    accent: str  # = focus: selection, lit buttons
    green: str  # = marked
    amber: str  # = warning (unpaired raw)
    stage: str  # behind the picture
    # every token name
    raised: str
    hover: str
    pressed: str
    text_dim: str
    text_disabled: str
    focus: str
    on_focus: str
    marked: str
    rejected: str
    cropped: str
    warning: str
    hud: str  # "rgba(r,g,b,a)": parse it with qcolor()
    key_chip_bg: str
    key_chip_text: str


def _colors(dark: bool, t: dict) -> Colors:
    return Colors(dark=dark, window=t["window"], panel=t["panel"], base=t["panel"], text=t["text"],
                  dim=t["text_dim"], border=t["border"], accent=t["focus"], green=t["marked"],
                  amber=t["warning"], stage=t["stage"], raised=t["raised"], hover=t["hover"],
                  pressed=t["pressed"], text_dim=t["text_dim"], text_disabled=t["text_disabled"],
                  focus=t["focus"], on_focus=t["on_focus"], marked=t["marked"], rejected=t["rejected"],
                  cropped=t["cropped"], warning=t["warning"], hud=t["hud"],
                  key_chip_bg=t["key_chip_bg"], key_chip_text=t["key_chip_text"])


DARK = _colors(True, tk.DARK)
LIGHT = _colors(False, tk.LIGHT)

_current = DARK
_RGBA = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")


def current() -> Colors:
    return _current


def qcolor(spec: str, alpha: int | None = None) -> QColor:
    """A QColor from a token value ("#rrggbb" or "rgba(r,g,b,a)"), optionally with its alpha replaced."""
    m = _RGBA.fullmatch(spec.strip())
    col = QColor(int(m[1]), int(m[2]), int(m[3]), int(m[4])) if m else QColor(spec)
    if alpha is not None:
        col.setAlpha(max(0, min(255, int(alpha))))
    return col


def is_dark(app: QGuiApplication | None = None) -> bool:
    app = app or QGuiApplication.instance()
    if app is None:
        return True
    return app.styleHints().colorScheme() != Qt.ColorScheme.Light


def palette(c: Colors) -> QPalette:
    p = QPalette()
    for role, col in (
        (QPalette.Window, c.window), (QPalette.WindowText, c.text), (QPalette.Base, c.base),
        (QPalette.AlternateBase, c.raised), (QPalette.Text, c.text), (QPalette.Button, c.panel),
        (QPalette.ButtonText, c.text), (QPalette.Highlight, c.focus), (QPalette.HighlightedText, c.on_focus),
        (QPalette.ToolTipBase, c.raised), (QPalette.ToolTipText, c.text), (QPalette.PlaceholderText, c.text_dim),
        (QPalette.Mid, c.border), (QPalette.Dark, c.border), (QPalette.Link, c.focus),
    ):
        p.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c.text_disabled))
    return p


def qss(c: Colors) -> str:
    """The app-wide stylesheet, generated from the tokens. Widgets opt into a look by property or object name."""
    t, r = tk.TYPE, tk.RADIUS
    sm, ctl, sheet, strong = r["small"], r["control"], r["sheet"], t["weight_strong"]
    return f"""
QToolTip {{ background: {c.raised}; color: {c.text}; border: 1px solid {c.border};
           padding: 4px 8px; font-size: {t['body']}px; }}
QMenu {{ background: {c.raised}; color: {c.text}; border: 1px solid {c.border}; padding: 4px; }}
QMenu::item {{ padding: 5px 20px 5px 12px; border-radius: {sm}px; }}
QMenu::item:selected {{ background: {c.focus}; color: {c.on_focus}; }}
QMenu::item:disabled {{ color: {c.text_disabled}; background: transparent; }}
QMenu::separator {{ height: 1px; background: {c.border}; margin: 4px 8px; }}
QPushButton {{ background: {c.panel}; color: {c.text}; border: 1px solid {c.border};
              border-radius: {ctl}px; padding: 5px 14px; }}
QPushButton:hover {{ background: {c.hover}; }}
QPushButton:pressed {{ background: {c.pressed}; }}
QPushButton:disabled {{ color: {c.text_disabled}; }}
QLabel[chip="true"] {{ background: {c.key_chip_bg}; color: {c.key_chip_text}; border-radius: {sm}px;
                      padding: 1px 6px; font-size: {t['caption']}px; }}
QLabel[dim="true"] {{ color: {c.text_dim}; }}
QLabel[state="marked"] {{ color: {c.marked}; font-weight: {strong}; }}
QLabel[state="warning"] {{ color: {c.warning}; }}
#hud {{ background: {c.hud}; border-radius: {sheet}px; }}
QStatusBar {{ background: {c.window}; color: {c.text_dim}; border-top: 1px solid {c.border}; }}
QStatusBar::item {{ border: 0; }}
QSplitter::handle {{ background: {c.border}; }}
"""


def apply(app: QGuiApplication, dark: bool) -> Colors:
    """Install the palette and stylesheet for `dark` (Fusion honours the palette on every platform)."""
    global _current
    _current = DARK if dark else LIGHT
    c = _current
    app.setStyle("Fusion")
    app.setPalette(palette(c))
    app.setStyleSheet(qss(c))
    return c


# ---- who decides: a saved pick, else the system (macOS only), else dark ----------------------------

_follow_app: QGuiApplication | None = None
_follow_slot = None


def choice(prefs) -> str | None:
    """The saved manual pick, "dark" or "light"; None when there is none (or the value is junk)."""
    v = str(prefs.value(settings.THEME, "") or "").strip().lower()
    return v if v in ("dark", "light") else None


def following() -> bool:
    return _follow_slot is not None


def _stop_following() -> None:
    global _follow_app, _follow_slot
    if _follow_slot is not None and _follow_app is not None:
        try:
            _follow_app.styleHints().colorSchemeChanged.disconnect(_follow_slot)
        except (RuntimeError, TypeError):
            pass
    _follow_app, _follow_slot = None, None


def follow(app: QGuiApplication, on_change=None) -> None:
    """Apply the system scheme now and again whenever it changes, until a pick or start() stops it."""
    global _follow_app, _follow_slot
    _stop_following()

    def update(*_):
        if _follow_slot is not update:  # stopped while an emit was in flight
            return
        apply(app, is_dark(app))
        if on_change:
            on_change()

    _follow_app, _follow_slot = app, update
    app.styleHints().colorSchemeChanged.connect(update)
    update()


def start(app: QGuiApplication, prefs, on_change=None, platform: str = sys.platform) -> Colors:
    """Choose the theme at launch: a saved pick wins; else macOS follows the system; else dark."""
    _stop_following()
    pick = choice(prefs)
    if pick is None and platform == "darwin":
        follow(app, on_change)
        return _current
    c = apply(app, pick != "light")
    if on_change:
        on_change()
    return c


def choose(app: QGuiApplication, prefs, dark: bool, on_change=None) -> Colors:
    """A manual pick: saved, the system is no longer followed, applied now."""
    prefs.setValue(settings.THEME, "dark" if dark else "light")
    prefs.sync()
    _stop_following()
    c = apply(app, dark)
    if on_change:
        on_change()
    return c
