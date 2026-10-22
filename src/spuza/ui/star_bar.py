"""The 1-5 star rating widgets: `stars_text`, the shared star drawing, and the Quiet bar's clickable `StarBar`.

Stars use the theme's text colour (never a hue: the pills are the only user colour, the chrome stays token-grey).
The bar sits after the pill tray: five stars and a key chip (Ctrl+1-5, natively a command key on a Mac). A click on a
star sets that many stars; a click on the lit top star clears them. `set_stars` never emits.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from spuza.ui import theme
from spuza.ui.pill_tray import draw_chip, font_px, native

MAX_STARS = 5
FILLED, EMPTY = "★", "☆"
KEY_SEQ = "Ctrl+1"
WIDTH = 180
HEIGHT = 26
PITCH = 20  # one star's slot in the bar
STAR_R = 8.0  # the bar's star radius


def clamp_stars(n) -> int:
    try:
        return max(0, min(MAX_STARS, int(n)))
    except (TypeError, ValueError, OverflowError):
        return 0


def stars_text(n: int) -> str:
    """Five glyphs: `n` filled then the rest empty ("★★★★☆" for 4). `n` is clamped to 0..5."""
    n = clamp_stars(n)
    return FILLED * n + EMPTY * (MAX_STARS - n)


def key_cap() -> str:
    """The chip text: "Ctrl+1–5", or the command-key form on a Mac."""
    return f"{native('Ctrl+1')}–5"


def tip_for(n: int) -> str:
    return f"{n} star{'s' if n != 1 else ''} · {native(f'Ctrl+{n}')}"


def star_path(cx: float, cy: float, r: float) -> QPainterPath:
    pts = []
    for k in range(10):
        ang = -math.pi / 2 + k * math.pi / 5
        rad = r if k % 2 == 0 else r * 0.42
        pts.append(QPointF(cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    path = QPainterPath()
    path.addPolygon(QPolygonF(pts))
    path.closeSubpath()
    return path


def draw_star(p: QPainter, cx: float, cy: float, r: float, filled: bool, color: QColor) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if filled:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
    else:
        p.setPen(QPen(color, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(star_path(cx, cy, r))
    p.restore()


class StarBar(QWidget):
    """Five clickable stars and a key chip. `rated(n)`: the clicked star's n, or 0 when the lit top star is clicked."""

    rated = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("starBar")
        self._n = 0
        self.setFixedSize(WIDTH, HEIGHT)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_stars(self, n: int) -> None:
        """Light `n` stars (0..5). Never emits `rated`."""
        n = clamp_stars(n)
        if n != self._n:
            self._n = n
            self.update()

    def stars(self) -> int:
        return self._n

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(WIDTH, HEIGHT)

    def star_rect(self, n: int) -> QRectF:
        """The click slot of star `n` (1..5)."""
        return QRectF(2 + (n - 1) * PITCH, 0, PITCH, HEIGHT)

    def _star_at(self, pos) -> int:
        for n in range(1, MAX_STARS + 1):
            if self.star_rect(n).contains(QPointF(pos)):
                return n
        return 0

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        n = self._star_at(e.position())
        if n:
            self.rated.emit(0 if n == self._n else n)

    def event(self, ev) -> bool:
        if ev.type() == QEvent.Type.ToolTip:
            n = self._star_at(ev.pos())
            if n:
                QToolTip.showText(ev.globalPos(), tip_for(n), self)
                return True
            QToolTip.hideText()
        return super().event(ev)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        c = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = HEIGHT / 2
        for n in range(1, MAX_STARS + 1):
            lit = n <= self._n
            draw_star(p, self.star_rect(n).center().x(), cy, STAR_R, lit, QColor(c.text if lit else c.text_dim))
        draw_chip(p, 2 + MAX_STARS * PITCH + 8, cy, key_cap(), font_px(self.font(), 10, True))
        p.end()
