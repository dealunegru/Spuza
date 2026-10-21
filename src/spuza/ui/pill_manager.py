"""The pill manager (Ctrl+Shift+P): make, rename, recolour and delete pills (direction A, digit pills).

A list of the user's pills on the left, each with how many pictures of this folder carry it and its digit; the selected
pill's name, one of the eight swatches (named underneath) and its digit on the right. Every change goes through the
PillBook and, for a rename or a delete, to the PillIndex too, then `changed` tells the window to redraw. Nothing is
written here: the window saves the book and the index (debounced, and on close).

Keys: Enter is Done (or confirms a delete), Esc closes (or cancels a delete), Ctrl+N makes a new pill, Ctrl+D asks to
delete the selected one. Every button names its key in a chip and in its tooltip.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolTip,
                               QVBoxLayout, QWidget)

from spuza import pills as pills_mod
from spuza.ui import theme
from spuza.ui import tokens as tk
from spuza.ui.pill_tray import chip_width, draw_chip, draw_dot, font_px, native
from spuza.ui.quiet_bar import tooltip_text

NEW_SEQ, DELETE_SEQ = "Ctrl+N", "Ctrl+D"
ROW = 36
SWATCH_W = 54


def _photos(n: int) -> str:
    return f"{n} photo{'s' if n != 1 else ''}"


class _PillList(QWidget):
    """The pills, one row each: dot, name, count, digit chip. A click or Up/Down selects."""

    selected = Signal(int)  # row

    def __init__(self, mgr: PillManager):
        super().__init__()
        self.mgr = mgr
        self.sel = 0
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName("pillList")

    def sync(self) -> None:
        n = len(self.mgr.book.pills())
        self.setFixedHeight(max(ROW, ROW * n) + 8)
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        c, dark = theme.current(), theme.current().dark
        f, cf = font_px(self.font(), 13), font_px(self.font(), 10, True)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, pill in enumerate(self.mgr.book.pills()):
            r = QRect(4, 4 + i * ROW, self.width() - 8, ROW - 2)
            if i == self.sel:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(c.hover))
                p.drawRoundedRect(r, tk.RADIUS["control"], tk.RADIUS["control"])
            cy = r.center().y() + 0.5
            draw_dot(p, r.x() + 18, cy, 6, pill.color, dark)
            p.setFont(font_px(f, 13, i == self.sel))
            p.setPen(QColor(c.text))
            p.drawText(QRect(r.x() + 36, r.y(), r.width() - 160, r.height()), Qt.AlignmentFlag.AlignVCenter, pill.name)
            p.setFont(font_px(f, 11))
            p.setPen(QColor(c.text_dim))
            p.drawText(QRect(r.right() - 150, r.y(), 80, r.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       _photos(self.mgr.count_fn(pill.name)))
            key = str(pill.digit)
            draw_chip(p, r.right() - 10 - chip_width(key, cf), cy, key, cf)
        p.end()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        row = (int(e.position().y()) - 4) // ROW
        if 0 <= row < len(self.mgr.book.pills()):
            self.selected.emit(row)


class _Swatches(QWidget):
    """The eight fixed colours, each named underneath; a click picks one."""

    picked = Signal(str)

    def __init__(self):
        super().__init__()
        self.sel = "None"
        self.setFixedSize(len(pills_mod.SWATCHES) * SWATCH_W, 60)
        self.setMouseTracking(True)
        self.setObjectName("pillSwatches")

    def paintEvent(self, _ev) -> None:  # noqa: N802
        c, dark = theme.current(), theme.current().dark
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, s in enumerate(pills_mod.SWATCHES):
            cx = SWATCH_W / 2 + i * SWATCH_W
            on = s.name == self.sel
            if on:
                p.setPen(QPen(QColor(c.text), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, 20), 16.5, 16.5)
            draw_dot(p, cx, 20, 12, s.name, dark)
            p.setFont(font_px(self.font(), 10, on))
            p.setPen(QColor(c.text if on else c.text_dim))
            p.drawText(QRect(int(cx - SWATCH_W / 2), 42, SWATCH_W, 16), Qt.AlignmentFlag.AlignCenter, s.name)
        p.end()

    def _at(self, x: float) -> str | None:
        i = int(x) // SWATCH_W
        return pills_mod.SWATCHES[i].name if 0 <= i < len(pills_mod.SWATCHES) else None

    def mousePressEvent(self, e) -> None:  # noqa: N802
        name = self._at(e.position().x())
        if name:
            self.picked.emit(name)

    def event(self, ev) -> bool:
        if ev.type() == QEvent.Type.ToolTip:
            name = self._at(ev.pos().x())
            if name:
                QToolTip.showText(ev.globalPos(), name, self)
                return True
        return super().event(ev)


class _Digits(QWidget):
    """Nine slots 1-9: the selected pill's digit is boxed, another pill's digit shows its colour dot."""

    def __init__(self, mgr: PillManager):
        super().__init__()
        self.mgr = mgr
        self.digit = 0
        self.setFixedSize(9 * 38, 56)
        self.setObjectName("pillDigits")

    def paintEvent(self, _ev) -> None:  # noqa: N802
        c, dark = theme.current(), theme.current().dark
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(1, 10):
            owner = self.mgr.book.by_digit(i)
            mine = i == self.digit
            r = QRectF((i - 1) * 38 + 1, 1, 32, 32)
            p.setPen(QPen(QColor(c.text if mine else c.border), 2 if mine else 1))
            p.setBrush(QColor(c.hover) if mine else Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, tk.RADIUS["control"], tk.RADIUS["control"])
            p.setFont(font_px(self.font(), 13, mine))
            p.setPen(QColor(c.text if (mine or owner is None) else c.text_dim))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(i))
            if owner is not None and not mine:
                draw_dot(p, r.center().x(), r.bottom() + 11, 3.5, owner.color, dark)
        p.end()


class PillManager(QDialog):
    """Make, rename, recolour and delete pills. `count_fn(name)` is how many pictures of this folder carry the pill;
    `total` is how many pictures the folder has."""

    changed = Signal()

    def __init__(self, book, index, count_fn, total: int, parent=None):
        super().__init__(parent)
        self.book, self.index, self.count_fn, self.total = book, index, count_fn, int(total)
        self.setObjectName("pillManager")
        self.setWindowTitle("Pills")
        self.confirming = False
        self._row = 0
        c = theme.current()
        self.setStyleSheet(
            f"QDialog {{ background: {c.window}; }} QLabel {{ color: {c.text}; background: transparent; }}"
            f"QLabel[chip='true'] {{ background: {c.key_chip_bg}; color: {c.key_chip_text};"
            f" border-radius: {tk.RADIUS['small']}px; padding: 1px 6px; font-size: {tk.TYPE['caption']}px; }}"
            f"QLabel[cap='true'] {{ color: {c.text_dim}; font-size: {tk.TYPE['caption']}px; font-weight: "
            f"{tk.TYPE['weight_strong']}; padding-top: 10px; }}"
            f"QLabel[dim='true'] {{ color: {c.text_dim}; }}"
            f"QLineEdit {{ background: {c.panel}; color: {c.text}; border: 1px solid {c.focus};"
            f" border-radius: {tk.RADIUS['control']}px; padding: 7px 10px; font-size: {tk.TYPE['body']}px; }}"
            f"QFrame#mgrList {{ background: {c.panel}; border: 1px solid {c.border};"
            f" border-radius: {tk.RADIUS['sheet']}px; }}")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        title = QLabel("Pills")
        title.setStyleSheet(f"font-size: {tk.TYPE['title']}px; font-weight: {tk.TYPE['weight_strong']};")
        sub = QLabel("Coloured labels you put on pictures. They go with the copy (keywords + colour label) and are "
                     "read back when you open the folder again.")
        sub.setWordWrap(True)
        sub.setProperty("dim", True)
        root.addWidget(title)
        root.addWidget(sub)
        body = QHBoxLayout()
        body.setSpacing(18)
        left = QVBoxLayout()
        lf = QFrame()
        lf.setObjectName("mgrList")
        lf.setFixedWidth(330)
        lfl = QVBoxLayout(lf)
        lfl.setContentsMargins(0, 0, 0, 0)
        self.list = _PillList(self)
        self.list.selected.connect(self._select)
        lfl.addWidget(self.list)
        left.addWidget(lf)
        newrow = QHBoxLayout()
        self.new_button, new_box = self._btn("New pill", NEW_SEQ)
        newrow.addWidget(new_box)
        newrow.addStretch(1)
        left.addLayout(newrow)
        left.addStretch(1)
        body.addLayout(left)
        right = QVBoxLayout()
        right.setSpacing(2)
        right.addWidget(self._cap("NAME"))
        self.name = QLineEdit()
        self.name.setMinimumWidth(420)
        self.name.setObjectName("pillName")
        self.name.editingFinished.connect(self._commit_name)
        right.addWidget(self.name)
        self.problem = QLabel("")
        self.problem.setProperty("dim", True)
        self.problem.setObjectName("pillProblem")
        right.addWidget(self.problem)
        right.addWidget(self._cap("COLOUR"))
        self.swatches = _Swatches()
        self.swatches.picked.connect(self._recolor)
        right.addWidget(self.swatches)
        self.finder = QLabel("")
        self.finder.setProperty("dim", True)
        right.addWidget(self.finder)
        right.addWidget(self._cap("DIGIT KEY"))
        self.digits = _Digits(self)
        right.addWidget(self.digits)
        right.addWidget(self._cap("USED ON"))
        self.used = QLabel("")
        right.addWidget(self.used)
        right.addStretch(1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        self.warn = QLabel("")
        self.warn.setObjectName("pillConfirm")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color: {c.text}; background: {c.raised}; border: 1px solid {c.border};"
                                f" border-radius: {tk.RADIUS['control']}px; padding: 10px 12px;")
        root.addWidget(self.warn)
        foot = QHBoxLayout()
        self.delete_button, self._delete_box = self._btn("Delete…", DELETE_SEQ)
        self.keep_button, self._keep_box = self._btn("Keep it", "Esc")
        self.confirm_button, self._confirm_box = self._btn("Delete pill", "Enter", strong=True)
        self.done_button, self._done_box = self._btn("Done", "Enter", strong=True)
        foot.addWidget(self._delete_box)
        foot.addStretch(1)
        for box in (self._keep_box, self._confirm_box, self._done_box):
            foot.addWidget(box)
        root.addLayout(foot)
        self.new_button.clicked.connect(self.new_pill)
        self.delete_button.clicked.connect(self.ask_delete)
        self.keep_button.clicked.connect(self.cancel_delete)
        self.confirm_button.clicked.connect(self.confirm_delete)
        self.done_button.clicked.connect(self.finish)
        for seq, fn in ((NEW_SEQ, self.new_pill), (DELETE_SEQ, self.ask_delete)):
            sc = QShortcut(QKeySequence(seq), self, activated=fn)
            sc.setAutoRepeat(False)
        self.resize(820, 560)
        self._sync()

    # ---- building blocks ----------------------------------------------------------------------
    def _cap(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setProperty("cap", True)
        return lab

    def _btn(self, text: str, seq: str, strong: bool = False):
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        key = native(seq)
        b = QPushButton(text)
        b.setToolTip(tooltip_text(text.rstrip("…"), key))  # "Label  Key": the policy test reads it
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setAutoDefault(False)
        b.setDefault(False)
        b.cap = key
        if strong:
            c = theme.current()
            b.setStyleSheet(f"QPushButton {{ background: {c.focus}; color: {c.on_focus}; border: none;"
                            f" font-weight: {tk.TYPE['weight_strong']}; padding: 5px 14px; }}")
        ch = QLabel(key)
        ch.setProperty("chip", True)
        lay.addWidget(b)
        lay.addWidget(ch)
        b.chip = ch
        return b, box

    # ---- state --------------------------------------------------------------------------------
    def selected_pill(self):
        pills = self.book.pills()
        return pills[self._row] if 0 <= self._row < len(pills) else None

    def _sync(self) -> None:
        """Redraw everything from the book."""
        pills = self.book.pills()
        self._row = min(max(self._row, 0), max(0, len(pills) - 1))
        self.list.sel = self._row
        self.list.sync()
        pill = self.selected_pill()
        self.name.setEnabled(pill is not None)
        self.name.setText(pill.name if pill else "")
        self.swatches.sel = pill.color if pill else ""
        self.swatches.update()
        self.digits.digit = pill.digit if pill else 0
        self.digits.update()
        self.finder.setText(f"Written as the Finder tag colour “{pills_mod.swatch(pill.color).name}”" if pill else
                            "No pills yet: New pill makes the first one.")
        n = self.count_fn(pill.name) if pill else 0
        self.used.setText(f"{n} of {self.total} pictures in this folder" if pill else "")
        self.new_button.setEnabled(len(pills) < pills_mod.MAX_PILLS)
        self.new_button.setToolTip(tooltip_text("New pill", native(NEW_SEQ)) if len(pills) < pills_mod.MAX_PILLS else
                                   tooltip_text(f"At most {pills_mod.MAX_PILLS} pills", native(NEW_SEQ)))
        self.delete_button.setEnabled(pill is not None)
        self._sync_footer()

    def _sync_footer(self) -> None:
        pill = self.selected_pill()
        confirming = self.confirming and pill is not None
        self.confirming = confirming
        self.warn.setVisible(confirming)
        if confirming:
            n = self.count_fn(pill.name)
            self.warn.setText(f"Delete “{pill.name}”? It comes off {n} picture{'s' if n != 1 else ''}. "
                              "Copies already made keep their tag.")
        self._delete_box.setVisible(not confirming)
        self._done_box.setVisible(not confirming)
        self._keep_box.setVisible(confirming)
        self._confirm_box.setVisible(confirming)

    def _select(self, row: int) -> None:
        self._commit_name()
        self.confirming = False
        self._row = row
        self.problem.setText("")
        self._sync()

    def _announce(self) -> None:
        self.changed.emit()

    # ---- the verbs ----------------------------------------------------------------------------
    def new_pill(self) -> None:
        if len(self.book.pills()) >= pills_mod.MAX_PILLS:
            return
        self._commit_name()
        used = {p.color for p in self.book.pills()}
        color = next((s.name for s in pills_mod.SWATCHES[1:] if s.name not in used), "Gray")
        name, n = "New pill", 1
        while self.book.by_name(name) is not None:
            n += 1
            name = f"New pill {n}"
        pill = self.book.create(name, color)
        self.confirming = False
        self._row = [p.digit for p in self.book.pills()].index(pill.digit)
        self.problem.setText("")
        self._sync()
        self.name.setFocus()
        self.name.selectAll()
        self._announce()

    def _commit_name(self) -> None:
        pill = self.selected_pill()
        if pill is None:
            return
        new = self.name.text().strip()
        if new == pill.name:
            return
        try:
            renamed = self.book.rename(pill.name, new)
        except ValueError as e:
            self.problem.setText(str(e).capitalize() + ".")
            self.name.setText(pill.name)
            return
        self.index.rename_pill(pill.name, renamed.name)
        self.problem.setText("")
        self._sync()
        self._announce()

    def _recolor(self, color: str) -> None:
        pill = self.selected_pill()
        if pill is None:
            return
        self.book.recolor(pill.name, color)
        self._sync()
        self._announce()

    def ask_delete(self) -> None:
        if self.selected_pill() is None:
            return
        self._commit_name()
        self.confirming = True
        self._sync_footer()

    def cancel_delete(self) -> None:
        self.confirming = False
        self._sync_footer()

    def confirm_delete(self) -> None:
        pill = self.selected_pill()
        if pill is None or not self.confirming:
            return
        self.book.delete(pill.name)
        self.index.drop_pill(pill.name)
        self.confirming = False
        self._sync()
        self._announce()

    def finish(self) -> None:
        self._commit_name()
        self.accept()

    # ---- keys ---------------------------------------------------------------------------------
    def keyPressEvent(self, e) -> None:  # noqa: N802
        k = e.key()
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not e.isAutoRepeat():
                self.confirm_delete() if self.confirming else self.finish()
        elif k == Qt.Key.Key_Escape:
            if not e.isAutoRepeat():
                self.cancel_delete() if self.confirming else self.reject()
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down) and not self.name.hasFocus():
            n = len(self.book.pills())
            if n:
                self._select(min(max(self._row + (1 if k == Qt.Key.Key_Down else -1), 0), n - 1))
        else:
            return super().keyPressEvent(e)
        e.accept()
