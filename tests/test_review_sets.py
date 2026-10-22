"""Review sets (Spuza 448 + 449): one sidebar row per pill beside All / Marked, and opening a set shows its pictures big,
steppable like the main screen (J / K / Left / Right within the set, no wrap), Esc to the set's grid, Esc again to leave.

Offscreen QApplication, real MainWindow on tiny synthetic JPEGs in tmp_path; SPUZA_PILLS_DIR is isolated per test.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza.catalog import Catalog, Prefetcher, scan
from spuza.ui import theme
from spuza.ui.review import PAGE_EMPTY, PAGE_GRID, PAGE_ZOOM
from spuza.ui.window import MainWindow

APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_pills(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_PILLS_DIR", str(tmp_path / "pills"))
    theme._stop_following()
    yield
    theme._stop_following()
    theme._current = theme.DARK
    APP.setStyleSheet("")
    APP.setPalette(QPalette())


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond(), "condition not reached in time"


def make_window(folder: Path, n: int = 6) -> MainWindow:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (64, 48), (40 * (i % 5) + 30, 90, 160)).save(folder / f"P{i}.jpg")
    cat = Catalog(scan(folder))
    w = MainWindow(cat, Prefetcher(cat), folder)
    w.resize(1200, 800)
    w.show()
    pump(lambda: w.viewer.has_image)
    return w


def give(w: MainWindow, name: str, which) -> None:
    """Put pill `name` on the pictures at the catalog indices `which`, the way the digit keys do."""
    pics = [w.catalog.pictures[i] for i in which]
    for p in pics:
        w.pill_index.toggle(p, name)
    w._pills_changed(pics)


@pytest.fixture
def win(tmp_path):
    w = make_window(tmp_path / "card")
    w.pill_book.create("Hero", "Blue")
    w.pill_book.create("Later", "Green")
    w.pill_book.create("Empty", "Red")
    w._pills_edited()
    give(w, "Hero", [1, 3, 5])
    give(w, "Later", [0, 3])
    yield w
    w.close()


def zoom_idx(w):
    return w.review._zoom_index


def test_sidebar_has_a_row_per_pill_beside_all_and_marked(win):
    sb = win.sidebar
    assert sb.row_names() == ["All", "Marked"]  # the fixed rows are untouched
    assert sb.pill_row_names() == ["Hero", "Later", "Empty"]  # the book's order
    assert sb.pill_counts() == {"Hero": 3, "Later": 2, "Empty": 0}  # a pill with no picture still shows


def test_rows_follow_a_toggle_a_rename_and_a_delete(win):
    sb = win.sidebar
    win.go_to(2)
    win.setFocus()
    QTest.keyClick(win, Qt.Key_1)  # digit 1 is Hero
    assert sb.pill_counts()["Hero"] == 4
    QTest.keyClick(win, Qt.Key_1)
    assert sb.pill_counts()["Hero"] == 3
    win.pill_book.rename("Hero", "Star")
    win.pill_index.rename_pill("Hero", "Star")
    win._pills_edited()
    assert sb.pill_row_names() == ["Star", "Later", "Empty"] and sb.pill_counts()["Star"] == 3
    win.pill_book.delete("Later")
    win._pills_edited()
    assert sb.pill_row_names() == ["Star", "Empty"]
    win.pill_book.create("Fresh", "Orange")
    win._pills_edited()
    assert sb.pill_row_names() == ["Star", "Fresh", "Empty"]  # the book gives a new pill the lowest free digit
    assert sb.pill_counts()["Fresh"] == 0


def test_clicking_a_pill_row_opens_zoom_on_the_sets_first_picture(win):
    win.sidebar.pill_row("Hero").clicked.emit()
    r = win.review
    assert win.stack.currentIndex() == 1
    assert r.stack.currentIndex() == PAGE_ZOOM and zoom_idx(win) == 1
    assert r.set_kind == "pill" and r.set_title() == "Hero"
    assert "1 of 3" in r.zoom_caption.text() and "Hero" in r.zoom_caption.text()
    assert win.sidebar.open_row() == "Hero"


def test_a_real_click_on_the_row_opens_it(win):
    row = win.sidebar.pill_row("Later")
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 5))
    assert win.stack.currentIndex() == 1 and win.review.set_name == "Later" and zoom_idx(win) == 0


def test_opening_lands_on_the_viewer_picture_when_it_is_in_the_set(win):
    win.go_to(3)
    win.sidebar.pill_row("Hero").clicked.emit()
    assert zoom_idx(win) == 3
    win.close_review()
    win.go_to(2)  # not in Hero
    win.sidebar.pill_row("Hero").clicked.emit()
    assert zoom_idx(win) == 1


def test_steps_stay_inside_the_set_and_stop_at_the_ends(win):
    win.sidebar.pill_row("Hero").clicked.emit()  # members 1, 3, 5
    r = win.review
    seen = [zoom_idx(win)]
    for key in (Qt.Key_K, Qt.Key_Right, Qt.Key_K):  # the third K is past the end
        QTest.keyClick(r.zoom_view, key)
        seen.append(zoom_idx(win))
    assert seen == [1, 3, 5, 5]
    assert "last picture of Hero" in r.zoom_caption.text() and win.last_message == "Last picture of Hero"
    assert "3 of 3" in r.zoom_caption.text()
    for key in (Qt.Key_J, Qt.Key_Left, Qt.Key_J):
        QTest.keyClick(r.zoom_view, key)
        seen.append(zoom_idx(win))
    assert seen[4:] == [3, 1, 1]
    assert "first picture of Hero" in r.zoom_caption.text()
    assert win.catalog.index == 0  # the viewer did not move


def test_keys_reach_the_zoom_page_through_the_window(win):
    win.sidebar.pill_row("Hero").clicked.emit()
    win.keyPressEvent(_key(Qt.Key_K))
    assert zoom_idx(win) == 3
    win.buttons["next"].click()
    assert zoom_idx(win) == 5
    win.buttons["back"].click()
    assert zoom_idx(win) == 3


def _key(k):
    from PySide6.QtGui import QKeyEvent

    return QKeyEvent(QKeyEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier)


def test_escape_goes_to_the_grid_then_leaves_review(win):
    win.sidebar.pill_row("Hero").clicked.emit()
    r = win.review
    QTest.keyClick(r.zoom_view, Qt.Key_K)
    QTest.keyClick(r.zoom_view, Qt.Key_Escape)
    assert win.stack.currentIndex() == 1 and r.stack.currentIndex() == PAGE_GRID
    assert [r.model.item(k).data(Qt.ItemDataRole.UserRole + 1) for k in range(r.model.rowCount())] == [1, 3, 5]
    assert r.selected_index() == 3  # the grid sits on the picture just left
    QTest.keyClick(r.view, Qt.Key_Escape)
    assert win.stack.currentIndex() == 0
    assert win.sidebar.open_row() is None


def test_the_marked_set_still_works(win):
    for i in (2, 4):
        win.catalog.set_mark(i, True)
    win.catalog.go(4)
    win.sidebar._rows[1][1].click()  # Marked
    r = win.review
    assert r.set_kind == "marked" and r.stack.currentIndex() == PAGE_ZOOM and zoom_idx(win) == 4
    assert "2 of 2" in r.zoom_caption.text() and "Marked" in r.zoom_caption.text()
    QTest.keyClick(r.zoom_view, Qt.Key_J)
    assert zoom_idx(win) == 2
    QTest.keyClick(r.zoom_view, Qt.Key_M)  # unmark the one on screen: the next remaining one shows
    assert not win.catalog.is_marked(2) and zoom_idx(win) == 4
    QTest.keyClick(r.zoom_view, Qt.Key_M)  # the last one: the set is empty
    assert r.stack.currentIndex() == PAGE_EMPTY and win.catalog.marked_count == 0
    assert win.sidebar.open_row() == "Marked"


def test_the_review_key_opens_marked_on_zoom(win):
    win.catalog.set_mark(2, True)
    win.catalog.go(2)
    win.toggle_review()
    assert win.stack.currentIndex() == 1 and win.review.stack.currentIndex() == PAGE_ZOOM and zoom_idx(win) == 2
    win.toggle_review()
    assert win.stack.currentIndex() == 0


def test_all_closes_review(win):
    win.sidebar.pill_row("Hero").clicked.emit()
    win.sidebar._rows[0][1].click()
    assert win.stack.currentIndex() == 0 and win.sidebar.open_row() is None


def test_return_opens_the_picture_in_the_viewer(win):
    win.sidebar.pill_row("Hero").clicked.emit()
    QTest.keyClick(win.review.zoom_view, Qt.Key_K)
    QTest.keyClick(win.review.zoom_view, Qt.Key_Return)
    assert win.stack.currentIndex() == 0 and win.catalog.index == 3


def test_unpilling_the_current_picture_advances(win):
    win.sidebar.pill_row("Hero").clicked.emit()  # 1, 3, 5
    r = win.review
    QTest.keyClick(r.zoom_view, Qt.Key_K)  # on 3
    win.keyPressEvent(_key(Qt.Key_1))  # digit 1 is Hero: off the picture on screen
    assert zoom_idx(win) == 5 and r.stack.currentIndex() == PAGE_ZOOM
    assert win.sidebar.pill_counts()["Hero"] == 2
    win.keyPressEvent(_key(Qt.Key_1))  # 5 was the last: the previous one that remains
    assert zoom_idx(win) == 1
    win.keyPressEvent(_key(Qt.Key_1))  # the last one: the set is empty
    assert r.stack.currentIndex() == PAGE_EMPTY and win.sidebar.pill_counts()["Hero"] == 0


def test_a_pill_with_no_pictures_opens_the_empty_page(win):
    win.sidebar.pill_row("Empty").clicked.emit()
    assert win.stack.currentIndex() == 1 and win.review.stack.currentIndex() == PAGE_EMPTY
    assert "Empty" in win.review.empty.text()


def test_the_open_set_follows_a_rename_and_closes_on_delete(win):
    win.sidebar.pill_row("Hero").clicked.emit()
    win.pill_book.rename("Hero", "Star")
    win.pill_index.rename_pill("Hero", "Star")
    win._pills_edited()
    assert win.review.set_name == "Star" and win.sidebar.open_row() == "Star" and zoom_idx(win) == 1
    win.pill_book.delete("Star")
    win._pills_edited()
    assert win.stack.currentIndex() == 0 and "Star" in win.last_message


def test_switching_sets_inside_review(win):
    win.sidebar.pill_row("Hero").clicked.emit()  # 1, 3, 5
    QTest.keyClick(win.review.zoom_view, Qt.Key_K)  # on 3, which Later has too
    win.sidebar.pill_row("Later").clicked.emit()
    assert win.review.set_name == "Later" and win.sidebar.open_row() == "Later" and zoom_idx(win) == 3
    win.sidebar.pill_row("Hero").clicked.emit()
    assert zoom_idx(win) == 3
    QTest.keyClick(win.review.zoom_view, Qt.Key_K)
    win.sidebar.pill_row("Later").clicked.emit()  # on 5, which Later lacks: Later's first picture
    assert zoom_idx(win) == 0


def test_pill_rows_are_not_buttons_so_the_key_policy_has_nothing_to_name(win):
    from PySide6.QtWidgets import QAbstractButton

    row = win.sidebar.pill_row("Hero")
    assert not row.findChildren(QAbstractButton) and not isinstance(row, QAbstractButton)
    assert [b.text() for b in win.sidebar.findChildren(QAbstractButton)] == ["All", "Marked"]
