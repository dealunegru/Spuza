"""The ratio strip: a row of chips above the picture, shown only while the X crop edit is open (round 6, mockup A).

`1 Free` `2 Original` `3 1:1` `4 4:5` `5 3:4` `6 2:3` `7 9:16` `8 1.91:1`, then `V turn`. The current chip is filled
with the accent and shows its on-screen label (5:4 after V). A click does what the key does: it emits `picked(entry)`
or `turn()`; the window owns the behaviour. Never takes keyboard focus. Cheap numbers only: no decoding here.

API: set_state(ratio, turns) / chip_texts() / current_entry() / retheme(), signals `picked(int)`, `turn()`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from spuza import ratio as ratio_mod
from spuza.ui import theme

BAR_H = 42


class Chip(QLabel):
    """A clickable label. Not a QAbstractButton on purpose: the review-controls table counts every button in the
    window, and a chip is no review control. Never takes focus."""

    clicked = Signal()

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def click(self) -> None:
        self.clicked.emit()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            e.accept()
        else:
            e.ignore()


class RatioStrip(QWidget):
    picked = Signal(int)
    turn = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BAR_H)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._r = ratio_mod.FREE
        self._turns = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(6)
        self._title = QLabel("Crop ratio", self)
        lay.addWidget(self._title)
        self._chips: dict[int, Chip] = {}
        for entry, name in ratio_mod.ENTRIES:
            b = self._chip(f"{entry} {name}")
            b.clicked.connect(lambda _c=False, e=entry: self.picked.emit(e))
            self._chips[entry] = b
            lay.addWidget(b)
        self._turn = self._chip("V turn")
        self._turn.clicked.connect(lambda _c=False: self.turn.emit())
        lay.addWidget(self._turn)
        lay.addStretch(1)
        self.retheme()

    def _chip(self, text: str) -> Chip:
        return Chip(text, self)

    def set_state(self, r, turns: int = 0) -> None:
        self._r, self._turns = r, int(turns)
        for entry, name in ratio_mod.ENTRIES:
            on = entry == r.entry
            self._chips[entry].setText(f"{entry} {ratio_mod.label(r, self._turns) if on else name}")
        self.retheme()

    def chip_texts(self) -> list[str]:
        return [self._chips[e].text() for e, _n in ratio_mod.ENTRIES] + [self._turn.text()]

    def current_entry(self) -> int:
        return self._r.entry

    def retheme(self) -> None:
        c = theme.current()
        self.setStyleSheet(f"RatioStrip {{ background: {c.panel}; border-bottom: 1px solid {c.border}; }}")
        self._title.setStyleSheet(f"color: {c.dim}; font-size: 12px;")
        base = (f"color: {c.text}; background: {c.window}; border: 1px solid {c.border};"
                f" border-radius: 10px; padding: 3px 10px; font-size: 12px;")
        on = (f"color: {c.on_focus}; background: {c.focus}; border: 1px solid {c.focus};"
              f" border-radius: 10px; padding: 3px 10px; font-size: 12px; font-weight: 600;")
        for entry, b in self._chips.items():
            b.setStyleSheet(on if entry == self._r.entry else base)
        self._turn.setStyleSheet(base)
