"""Friend round 4: classic has one toolbar row. Mark all and the two turns did not fit it by the measured rule
(Copy... and Preferences... visible, 40 px spare at 1280 px), so they are hotkeys only."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest

from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui import window as window_mod
from spuza.ui.window import MainWindow

ROOT = Path("/cards/X")


def loader(path, max_side):
    return Image.new("RGB", (64, 48), (90, 120, 200)), PreviewInfo(path, "pillow", 64, 48, 10, 1.0)


def test_one_classic_row_and_the_three_actions_are_hotkeys_only():
    pics = [Picture(f"P{i}", ROOT / f"P{i}.JPG", None, (ROOT / f"P{i}.JPG",), "JPG", 1_000_000, 1.0)
            for i in range(3)]
    cat = Catalog(pics)
    win = MainWindow(cat, Prefetcher(cat, loader=loader), ROOT)
    win.show()
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 3000)
    try:
        assert not hasattr(win, "toolbar3") and not hasattr(window_mod, "ROW2_BUTTONS")
        assert not {"markall", "rotl", "rotr"} & set(win.buttons)
        assert all(b.parent() is win.toolbar for b in win.buttons.values())
        keys = {s.key().toString() for s in win.findChildren(QShortcut)}
        assert {QKeySequence(k).toString() for k in ("Ctrl+A", "Ctrl+Shift+A", "Ctrl+Z", "[", "]")} <= keys
        QTest.keyClick(win, Qt.Key_A, Qt.KeyboardModifier.ControlModifier)
        assert cat.marked_count == 3
        QTest.keyClick(win, Qt.Key_BracketRight)
        assert win.rotations == {0: 1}
    finally:
        win.close()
