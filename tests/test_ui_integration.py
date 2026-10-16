"""MainWindow with the REAL ReviewGrid: keys must reach the grid while review is open.

Keys go to QApplication.focusWidget(), never to the window, as a real keystroke would. Offscreen
window activation is unreliable, so the tests rely on the window's own setFocus calls and read
focusWidget() back.
"""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui.window import MainWindow

ROOT = Path("/cards/X")


def loader(path, max_side):
    return Image.new("RGB", (64, 48), (90, 120, 200)), PreviewInfo(path, "pillow", 64, 48, 10, 1.0)


def make(n=6):
    pics = [Picture(f"P{i}", ROOT / f"P{i}.JPG", None, (ROOT / f"P{i}.JPG",), "JPG", 1_000_000, 1.0)
            for i in range(n)]
    cat = Catalog(pics)
    return MainWindow(cat, Prefetcher(cat, loader=loader), ROOT)


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond()


def press(k):
    # a real keystroke goes to the focus widget, or to the active window when nothing has focus
    w = QApplication.focusWidget() or QApplication.activeWindow()
    assert w is not None, "no focus widget and no active window"
    QTest.keyClick(w, k)


def open_review(marked=(1, 3)):
    w = make()
    w.review_factory = None  # the real ReviewGrid through the default lazy factory
    w.resize(1000, 700)
    w.show()
    w.activateWindow()
    QTest.qWaitForWindowActive(w, 3000)  # bounded wait
    pump(lambda: w.viewer.has_image)
    for i in marked:
        w.catalog.set_mark(i, True)
    w.refresh_marks()
    QTest.keyClick(w, Qt.Key_R)
    assert w.stack.currentIndex() == 1
    return w


def test_focus_is_on_the_grid_after_r():
    w = open_review()
    try:
        fw = QApplication.focusWidget()
        assert fw is w.review.view
    finally:
        w.close()


def test_space_in_review_unmarks_grid_selection():
    w = open_review()
    try:
        assert w.review.selected_index() == 1 and w.catalog.index == 0
        press(Qt.Key_Space)
        assert not w.catalog.is_marked(1) and w.catalog.is_marked(3)
        assert w.lbl_marked.text().startswith("1 marked")
        assert w.sidebar.counts()["Marked"] == 1
        assert w.buttons["mark"].isChecked()  # lit for the grid selection (3, marked), not viewer picture 0
        assert not w.catalog.is_marked(0)
    finally:
        w.close()


def test_arrows_move_grid_selection_not_viewer():
    w = open_review()
    try:
        press(Qt.Key_Right)
        assert w.review.selected_index() == 3
        assert w.catalog.index == 0
    finally:
        w.close()


def test_enter_opens_selected_row_in_viewer():
    w = open_review()
    try:
        press(Qt.Key_Right)
        press(Qt.Key_Return)
        assert w.stack.currentIndex() == 0 and w.catalog.index == 3
        assert QApplication.focusWidget() is w
    finally:
        w.close()


def test_z_opens_grid_zoom_and_esc_returns_to_grid():
    w = open_review()
    try:
        press(Qt.Key_Z)
        assert w.review.stack.currentIndex() == 2 and w.stack.currentIndex() == 1
        assert QApplication.focusWidget() is w.review.zoom_view
        press(Qt.Key_Escape)
        assert w.review.stack.currentIndex() == 1 and w.stack.currentIndex() == 1
        assert QApplication.focusWidget() is w.review.view
    finally:
        w.close()


def test_esc_on_grid_returns_to_viewer_with_focus_on_window():
    w = open_review()
    try:
        press(Qt.Key_Escape)
        assert w.stack.currentIndex() == 0
        assert QApplication.focusWidget() is w
        press(Qt.Key_Right)  # keys drive the viewer again
        assert w.catalog.index == 1
    finally:
        w.close()


def test_r_closes_review_and_empty_review_still_takes_esc():
    w = open_review(marked=())
    try:
        press(Qt.Key_Escape)  # nothing marked: the grid shows its empty page, Esc must still close
        assert w.stack.currentIndex() == 0
        QTest.keyClick(w, Qt.Key_R)
        assert w.stack.currentIndex() == 1
        press(Qt.Key_R)
        assert w.stack.currentIndex() == 0
    finally:
        w.close()


def test_f_is_inert_in_review_and_keys_keep_working():
    w = open_review()
    try:
        press(Qt.Key_F)
        assert not w.is_bleed and w.stack.currentIndex() == 1
        w.buttons["fullscreen"].click()  # the toolbar button is just as inert
        assert not w.is_bleed and not w.buttons["fullscreen"].isChecked()
        press(Qt.Key_Space)
        assert not w.catalog.is_marked(1) and w.catalog.is_marked(3)
    finally:
        w.close()


def test_window_forwards_review_keys_when_it_holds_focus():
    w = open_review(marked=(1, 3, 5))
    try:
        w.setFocus()  # wherever focus lands, the window must pass the grid's keys on
        assert QApplication.focusWidget() is w
        press(Qt.Key_Right)
        assert w.review.selected_index() == 3  # moved exactly one row: not handled twice
        assert w.catalog.index == 0
        w.setFocus()
        press(Qt.Key_Space)
        assert not w.catalog.is_marked(3) and w.catalog.marked_count == 2
        w.setFocus()
        press(Qt.Key_Return)
        assert w.stack.currentIndex() == 0 and w.catalog.index in (1, 5)
    finally:
        w.close()


def test_filmstrip_pick_during_review_keeps_grid_keys_alive():
    w = open_review()
    try:
        w.filmstrip.picked.emit(4)
        w.setFocus()
        press(Qt.Key_Z)
        assert w.review.stack.currentIndex() == 2 and w.stack.currentIndex() == 1
        w.setFocus()
        press(Qt.Key_Escape)  # leaves the grid's zoom only
        assert w.review.stack.currentIndex() == 1 and w.stack.currentIndex() == 1
        w.setFocus()
        press(Qt.Key_Space)
        assert not w.catalog.is_marked(1)
    finally:
        w.close()


def test_esc_order_with_bleed_then_review():
    w = make()
    w.resize(1000, 700)
    w.show()
    w.activateWindow()
    QTest.qWaitForWindowActive(w, 3000)
    pump(lambda: w.viewer.has_image)
    w.catalog.set_mark(1, True)
    w.refresh_marks()
    QTest.keyClick(w, Qt.Key_F)
    assert w.is_bleed
    QTest.keyClick(w, Qt.Key_R)
    assert w.stack.currentIndex() == 1 and w.is_bleed
    try:
        press(Qt.Key_Escape)  # first Esc: review closes, bleed stays
        assert w.stack.currentIndex() == 0 and w.is_bleed
        press(Qt.Key_Escape)  # second: bleed ends
        assert not w.is_bleed
    finally:
        w.close()
