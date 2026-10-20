"""Direction B, Lights out: a floating translucent pill over the photo.

Wired into MainWindow (``window.pill``, shown only in Lights out over the single view). Never takes keyboard focus,
so window keys keep working.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QWidget

from . import icons, theme
from . import tokens as tk
from .icons import parse_color

# tokens.json dark values, used when theme.Colors predates the new fields.
_DARK = {
    "hud": "rgba(16,16,16,200)", "text": "#e6e6e6", "text_dim": "#9e9e9e", "marked": "#34c759",
    "focus": "#5a9bff", "on_focus": "#ffffff", "key_chip_bg": "#3a3a3a", "key_chip_text": "#d0d0d0",
}
BOTTOM_MARGIN = 24


def _col(c, name: str) -> str:
    fb = _DARK.get(name, "#808080")
    if name == "text_dim":
        return getattr(c, "text_dim", getattr(c, "dim", fb))
    if name == "marked":
        return getattr(c, "marked", getattr(c, "green", fb))
    return getattr(c, name, fb)


class LightsOutPill(QWidget):
    copy_clicked = Signal()
    help_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("lightsOutPill")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._host: QWidget | None = None
        self._hud = parse_color(_DARK["hud"])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 6, 8, 6)
        lay.setSpacing(12)
        self._dot = QLabel("●")
        self._mark = QLabel("")
        self._pos = QLabel("")
        self._help = QPushButton("keys ?")
        self._help.setObjectName("pillHelp")
        self._help.setFlat(True)
        self._copy = QToolButton()
        self._copy.setObjectName("pillCopy")
        self._copy.setText("Copy")
        self._copy.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for w in (self._dot, self._mark, self._pos, self._help, self._copy):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            lay.addWidget(w)
        self._help.clicked.connect(self.help_clicked)
        self._copy.clicked.connect(self.copy_clicked)
        self.set_state(False, "")
        self.retheme()
        if parent is not None:
            self.attach(parent)

    # ---- public API -------------------------------------------------------------------------
    def attach(self, host: QWidget) -> None:
        """Float over ``host``, bottom-centre, following its resizes."""
        if self._host is not None:
            self._host.removeEventFilter(self)
        self._host = host
        if self.parentWidget() is not host:
            self.setParent(host)
        host.installEventFilter(self)
        self.reposition()
        self.raise_()

    def set_state(self, marked: bool, position_text: str) -> None:
        marked = bool(marked)
        self._dot.setVisible(marked)
        self._mark.setVisible(marked)
        self._mark.setText("Marked" if marked else "")
        self._pos.setText(position_text)
        self.adjustSize()
        self.reposition()

    def help_button(self) -> QPushButton:
        return self._help

    def copy_button(self) -> QToolButton:
        return self._copy

    def marked_text(self) -> str:
        return self._mark.text()

    def position_text(self) -> str:
        return self._pos.text()

    def hud_color(self):
        return self._hud

    def reposition(self) -> None:
        host = self._host
        if host is None:
            return
        self.adjustSize()
        x = max(0, (host.width() - self.width()) // 2)
        y = max(0, host.height() - self.height() - BOTTOM_MARGIN)
        self.move(x, y)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self._host and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.reposition()
        return False

    def retheme(self) -> None:
        c = theme.current()
        # The HUD is dark in both themes (tokens.json: rgba(16..24)), so its text, marked green and key chip are the
        # dark theme's; only the HUD itself and the Copy blue follow the current theme. (Real-platform grab, light B:
        # the light theme's dim grey on the dark HUD was unreadable.)
        d = tk.DARK
        text, dim, marked = d["text"], d["text_dim"], d["marked"]
        focus, on_focus = _col(c, "focus"), _col(c, "on_focus")
        chip_bg, chip_fg = d["key_chip_bg"], d["key_chip_text"]
        self._hud = parse_color(_col(c, "hud"))
        self._copy.setIcon(icons.icon("copy", on_focus, 16))
        self.setStyleSheet(f"""
            #lightsOutPill QLabel {{ color: {text}; background: transparent; }}
            #lightsOutPill #pillHelp {{ color: {chip_fg}; background: {chip_bg}; border: none;
                border-radius: 8px; padding: 2px 8px; }}
            #lightsOutPill #pillCopy {{ background: {focus}; color: {on_focus}; border: none;
                border-radius: 12px; padding: 4px 12px; font-weight: 600; }}
        """)
        self._dot.setStyleSheet(f"color: {marked}; background: transparent;")
        self._mark.setStyleSheet(f"color: {marked}; background: transparent;")
        self._pos.setStyleSheet(f"color: {dim}; background: transparent;")
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._hud)
        r = self.rect()
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.end()
