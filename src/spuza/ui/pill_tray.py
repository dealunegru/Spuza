"""The pill tray (Direction A, digit pills) and the drawing helpers every pill surface shares.

The tray sits in the Quiet bar after the title: one capsule per pill (dot, name, digit chip), lit when the current
picture carries it, a click toggles it, and a last "Edit" capsule with its key chip opens the manager. It never
pushes the bar's right-hand buttons off (decision spuza-pills-tray-1280): when the full capsules do not fit they drop
their names (dot + digit chip, the name moves to the tooltip), and if even that does not fit it shows as many as fit
plus a "+N" capsule that opens the Pills menu. The plan is pure arithmetic on font metrics: `PillTray.plan(width)`.

Colours: a pill's colour is a swatch NAME (spuza.pills); `swatch_color` picks the dark or light hex for the theme.
Over a photo (filmstrip, review, Lights out) use `dark=True` with `halo=True`: the dark swatch on a dark disc reads on
any picture in both themes. "None" is a hollow ring so it never looks like a missing dot.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from spuza import pills as pills_mod
from spuza.ui import theme
from spuza.ui import tokens as tk

MANAGE_SEQ = "Ctrl+Shift+P"
MANAGE_CAP = "⌘⇧P" if sys.platform == "darwin" else MANAGE_SEQ  # the Mac label; Qt maps Ctrl to Cmd there
GAP = 4
EDIT_GAP = 2  # extra room before the Edit capsule
HEIGHT = 26
DOT_R = 4.5
HALO = 1.7


def native(seq: str) -> str:
    """A key sequence as this platform writes it ("Ctrl+N" is "⌘N" on a Mac)."""
    return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText) or seq


def swatch_color(name: str, dark: bool) -> QColor:
    s = pills_mod.swatch(name)
    return QColor(s.dark if dark else s.light)


def tint(c: QColor, alpha: int) -> QColor:
    out = QColor(c)
    out.setAlpha(max(0, min(255, int(alpha))))
    return out


def font_px(base: QFont, px: int, bold: bool = False) -> QFont:
    f = QFont(base)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def draw_dot(p: QPainter, cx: float, cy: float, r: float, color_name: str, dark: bool, alpha: int = 255,
             halo: bool = False) -> None:
    """A pill's colour dot. `halo`: a dark disc behind it (over a photo). The None swatch is a hollow ring."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if halo:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(theme.current().hud, 175))
        p.drawEllipse(QPointF(cx, cy), r + HALO, r + HALO)
    c = tint(swatch_color(color_name, dark), alpha)
    if pills_mod.swatch(color_name).finder == 0:
        p.setPen(QPen(c, max(1.4, r * 0.34)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r - 0.8, r - 0.8)
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(cx, cy), r, r)
    p.restore()


def draw_dots_right(p: QPainter, right: float, cy: float, colors, r: float = 4.0, pitch: float = 10.0,
                    most: int = 6) -> None:
    """A row of halo dots ending at x=`right` (the bottom-right corner of a thumbnail or review cell)."""
    colors = list(colors)
    shown = colors[:most]
    x = right - r - HALO
    for name in reversed(shown):
        draw_dot(p, x, cy, r, name, True, halo=True)
        x -= pitch
    if len(colors) > most:  # more than fit: a small "+" before the dots
        p.save()
        p.setPen(QColor(tk.DARK["text"]))
        f = QFont(p.font())
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(x - 8, cy - 7, 12, 14), Qt.AlignmentFlag.AlignCenter, "+")
        p.restore()


def chip_width(text: str, font: QFont) -> int:
    return max(16, QFontMetrics(font).horizontalAdvance(text) + 10)


def draw_chip(p: QPainter, x: float, cy: float, text: str, font: QFont) -> int:
    c = theme.current()
    w = chip_width(text, font)
    r = QRectF(x, cy - 8, w, 16)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(c.key_chip_bg))
    p.drawRoundedRect(r, tk.RADIUS["small"], tk.RADIUS["small"])
    p.setPen(QColor(c.key_chip_text))
    p.setFont(font)
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    p.restore()
    return int(w)


@dataclass(frozen=True)
class Capsule:
    kind: str  # "pill" | "edit" | "more"
    label: str  # the pill's name, "Edit", or "+N"
    key: str  # the chip text: the digit, the manage key, or ""
    lit: bool
    short: bool  # drawn without its name
    rect: QRect
    color: str = "None"
    tip: str = ""


class PillTray(QWidget):
    """The capsules. `book_fn()` -> the pills in digit order; `lit_fn()` -> the (case-folded) names the current
    picture carries. Signals: toggled(name), manage(), more()."""

    toggled = Signal(str)
    manage = Signal()
    more = Signal()

    def __init__(self, book_fn, lit_fn, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("pillTray")
        self.book_fn, self.lit_fn = book_fn, lit_fn
        self.setFixedHeight(HEIGHT)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._caps: list[Capsule] = []

    # ---- the plan ---------------------------------------------------------------------------
    def _fonts(self) -> tuple[QFont, QFont]:
        return font_px(self.font(), 12), font_px(self.font(), 10, True)

    def _widths(self):
        f, cf = self._fonts()
        fm, fmb = QFontMetrics(f), QFontMetrics(font_px(f, 12, True))
        pills = list(self.book_fn())[:pills_mod.MAX_PILLS]
        full = [10 + 9 + 6 + fmb.horizontalAdvance(p.name) + 7 + chip_width(str(p.digit), cf) + 8 for p in pills]
        short = [10 + 9 + 6 + chip_width(str(p.digit), cf) + 8 for p in pills]
        edit = 10 + fm.horizontalAdvance("Edit") + 7 + chip_width(MANAGE_CAP, cf) + 8
        more = lambda n: 10 + fm.horizontalAdvance(f"+{n}") + 10  # noqa: E731
        return pills, full, short, edit, more

    def plan(self, width: int) -> list[Capsule]:
        """The capsules that fit in `width` px, left to right. Edit is always last; see the module docstring."""
        pills, full, short, edit_w, more_w = self._widths()
        lit = {str(n).casefold() for n in self.lit_fn()}
        base = EDIT_GAP + edit_w  # the Edit capsule, and the gap before it
        mode, n_show = "full", len(pills)
        if sum(w + GAP for w in full) + base > width:
            mode = "short"
            if sum(w + GAP for w in short) + base > width:
                mode = "count"
                n_show, x = 0, 0
                for i, w in enumerate(short):
                    hidden = len(pills) - (i + 1)
                    reserve = more_w(hidden) + GAP if hidden else 0
                    if x + w + GAP + reserve + base > width:
                        break
                    x += w + GAP
                    n_show = i + 1
        _f, cf = self._fonts()
        caps: list[Capsule] = []
        x = 0
        widths = full if mode == "full" else short
        for p, w in zip(pills[:n_show], widths):
            key = str(p.digit)
            is_short = mode != "full"
            tip = f"{'Remove' if p.name.casefold() in lit else 'Add'} {p.name}  {key}"
            caps.append(Capsule("pill", p.name, key, p.name.casefold() in lit, is_short, QRect(x, 1, w, HEIGHT - 2),
                                p.color, tip))
            x += w + GAP
        if mode == "count" and n_show < len(pills):
            n = len(pills) - n_show
            w = more_w(n)
            caps.append(Capsule("more", f"+{n}", "", False, False, QRect(x, 1, w, HEIGHT - 2), "None",
                                f"{n} more pill{'s' if n != 1 else ''}  open the Pills menu"))
            x += w + GAP
        caps.append(Capsule("edit", "Edit", MANAGE_CAP, False, False, QRect(x + EDIT_GAP, 1, edit_w, HEIGHT - 2), "None",
                            f"Manage pills  {MANAGE_CAP}"))
        return caps

    def capsules(self) -> list[Capsule]:
        """What is on screen now (the plan for the current width)."""
        return self.plan(max(self.width(), 1))

    def full_width(self) -> int:
        """The width the capsules need when nothing is collapsed."""
        _pills, full, _short, edit_w, _more = self._widths()
        return sum(w + GAP for w in full) + EDIT_GAP + edit_w

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.full_width(), HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        _pills, _full, _short, edit_w, more_w = self._widths()
        return QSize(EDIT_GAP + edit_w + GAP + more_w(9), HEIGHT)

    def refresh(self) -> None:
        self.updateGeometry()
        self.update()

    # ---- painting and mouse -----------------------------------------------------------------
    def paintEvent(self, _ev) -> None:  # noqa: N802
        c = theme.current()
        dark = c.dark
        f, cf = self._fonts()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._caps = self.capsules()
        for cap in self._caps:
            r = cap.rect
            cy = r.center().y() + 0.5
            rf = QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5)
            rad = rf.height() / 2
            if cap.kind == "pill":
                base = swatch_color(cap.color, dark)
                p.setPen(QPen(tint(base, 215) if cap.lit else QColor(c.border), 1))
                p.setBrush(tint(base, 52) if cap.lit else Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rf, rad, rad)
                draw_dot(p, r.x() + 14.5, cy, DOT_R, cap.color, dark, alpha=255 if cap.lit else 120)
                tx = r.x() + 10 + 9 + 6
                if not cap.short:
                    nf = font_px(f, 12, cap.lit)
                    p.setFont(nf)
                    p.setPen(QColor(c.text if cap.lit else c.text_dim))
                    p.drawText(QRect(tx, r.y(), r.width(), r.height()),
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, cap.label)
                    tx += QFontMetrics(nf).horizontalAdvance(cap.label) + 7
                draw_chip(p, tx, cy, cap.key, cf)
            else:
                p.setPen(QPen(QColor(c.border), 1, Qt.PenStyle.DashLine if cap.kind == "edit" else Qt.PenStyle.SolidLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rf, rad, rad)
                p.setFont(f)
                p.setPen(QColor(c.text_dim))
                if cap.kind == "more":
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter, cap.label)
                else:
                    tx = r.x() + 10
                    p.drawText(QRect(tx, r.y(), r.width(), r.height()),
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, cap.label)
                    tx += QFontMetrics(f).horizontalAdvance(cap.label) + 7
                    draw_chip(p, tx, cy, cap.key, cf)
        p.end()

    def _hit(self, pos) -> Capsule | None:
        for cap in self.capsules():
            if cap.rect.contains(pos):
                return cap
        return None

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        cap = self._hit(e.position().toPoint())
        if cap is None:
            return
        if cap.kind == "pill":
            self.toggled.emit(cap.label)
        elif cap.kind == "edit":
            self.manage.emit()
        else:
            self.more.emit()

    def event(self, ev) -> bool:
        if ev.type() == QEvent.Type.ToolTip:
            cap = self._hit(ev.pos())
            if cap is not None:
                QToolTip.showText(ev.globalPos(), cap.tip, self)
                return True
            QToolTip.hideText()
        return super().event(ev)
