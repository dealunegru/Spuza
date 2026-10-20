"""Every control, while review is shown, acts on what the user sees (the grid) or is disabled.

REVIEW_TABLE is the enumeration of the window's controls (found by walking findChildren on a live
window). A control that appears without a row here fails test_table_covers_every_control, so the next
one cannot slip in unhandled.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton

from spuza import features
from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui.window import MainWindow

ROOT = Path("/cards/X")

# control -> behaviour while review is shown
REVIEW_TABLE = {
    "back": "grid", "next": "grid", "mark": "grid", "advance": "global", "zoom": "grid",
    "fullscreen": "disabled", "review": "global", "copy": "global",
    "sidebar:All": "global", "sidebar:Marked": "global",
    "shortcut:F11": "disabled", "shortcut:A": "global", "filmstrip": "grid",
    "prefs": "global", "shortcut:Ctrl+,": "global", "shortcut:Ctrl+Q": "global",
    # friend round 3: Mark all acts on the whole card from anywhere; the turns act on the grid's selected pictures
    # (round 4: hotkeys only, no buttons)
    "shortcut:Ctrl+A": "global", "shortcut:Ctrl+Shift+A": "global", "shortcut:Ctrl+Z": "global",
    "shortcut:[": "grid", "shortcut:]": "grid",
    "review:Unmark": "grid", "review:Zoom": "grid",
    # advanced-only (hidden, never absent, in classic): N acts on the grid selection; P and O are global switches
    "compare": "grid", "peaking": "global", "afpoint": "global",
}


def loader(path, max_side):
    return Image.new("RGB", (64, 48), (90, 120, 200)), PreviewInfo(path, "pillow", 64, 48, 10, 1.0)


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond()


def _window():
    pics = [Picture(f"P{i}", ROOT / f"P{i}.JPG", None, (ROOT / f"P{i}.JPG",), "JPG", 1_000_000, 1.0)
            for i in range(6)]
    cat = Catalog(pics)
    win = MainWindow(cat, Prefetcher(cat, loader=loader), ROOT)
    win.resize(1000, 700)
    win.show()
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 3000)
    pump(lambda: win.viewer.has_image)
    for i in (1, 3, 5):
        cat.set_mark(i, True)
    win.refresh_marks()
    return win


@pytest.fixture
def w():
    win = _window()
    yield win
    win.close()


@pytest.fixture
def wa():
    features.set_mode(features.ADVANCED)
    win = _window()
    yield win
    win.close()


def in_review(w):
    w.buttons["review"].click()
    assert w.stack.currentIndex() == 1
    assert w.review.selected_index() == 1
    return w


# Widgets that are Qt internals, not controls of ours. Anything else a window owns must be in the table.
INTERNAL_BUTTONS = {"qt_toolbar_ext_button", "qt_menubar_ext_button"}  # the toolbar's and menu bar's overflow chevrons


def _control_key(b, w):
    name = b.objectName()
    if name in INTERNAL_BUTTONS:
        return None
    if name.startswith("btn_"):
        return name[4:]
    if w.sidebar.isAncestorOf(b):
        return f"sidebar:{b.text()}"
    if w.review is not None and w.review.isAncestorOf(b):
        return f"review:{b.text()}"  # the grid's own buttons: they act on the grid by construction
    return f"{type(b).__name__}:{b.text() or name}"  # unknown: not in the table, so the test fails


def test_table_covers_every_control(w):
    in_review(w)  # opens the review page so its buttons exist
    found = {"filmstrip"}
    for b in w.findChildren(QAbstractButton):
        k = _control_key(b, w)
        if k is not None:
            found.add(k)
    found |= {f"shortcut:{s.key().toString()}" for s in w.findChildren(QShortcut)}
    # the menu bar's QActions only display keys (WidgetShortcut, never fire): test_design_menu.py covers them
    found |= {f"shortcut:{a.shortcut().toString()}" for a in w.findChildren(QAction)
              if not a.shortcut().isEmpty() and a not in w.menu_actions.values()}
    assert found == set(REVIEW_TABLE), f"unlisted: {found - set(REVIEW_TABLE)}, stale: {set(REVIEW_TABLE) - found}"


def test_table_covers_every_control_advanced(wa):
    test_table_covers_every_control(wa)
    assert all(wa._next_actions[n].isVisible() for n in wa.next_buttons)  # (the widget may fold into the overflow)


def test_classic_has_the_new_controls_hidden_not_visible(w):
    assert not any(w._next_actions[n].isVisible() for n in w.next_buttons) and not w.toolbar2.isVisible()
    assert not any(b.isVisible() for b in w.next_buttons.values())


def test_peaking_and_afpoint_are_global_in_review_and_compare_acts_on_the_grid(wa):
    in_review(wa)
    wa.next_buttons["peaking"].click()  # a preference: toggles from anywhere, review stays, viewer does not move
    assert wa.peaking.is_on() and wa.stack.currentIndex() == 1 and wa.catalog.index == 0
    wa.next_buttons["afpoint"].click()
    assert wa.marker.is_on() and wa.stack.currentIndex() == 1
    wa.next_buttons["compare"].click()  # one selected picture: a hint, nothing opens
    assert wa.stack.currentIndex() == 1 and not wa._compare_shown() and "Select two" in wa.last_message
    wa.review.view.selectAll()
    wa.next_buttons["compare"].click()  # the grid's selection (1, 3, 5) is what opens
    assert wa._compare_shown() and wa.compare.indices() == [1, 3, 5] and wa.catalog.index == 0


def test_disabled_rows_are_disabled_in_review_and_reenabled(w):
    in_review(w)
    assert not w.buttons["fullscreen"].isEnabled()
    w.buttons["fullscreen"].click()
    assert not w.is_bleed
    w.review.closed.emit()
    assert all(b.isEnabled() for b in w.buttons.values())


def test_back_next_act_on_grid_selection(w):
    in_review(w)
    w.buttons["next"].click()
    assert w.review.selected_index() == 3 and w.catalog.index == 0
    w.buttons["back"].click()
    assert w.review.selected_index() == 1 and w.catalog.index == 0


def test_mark_button_acts_on_grid_selection(w):
    in_review(w)
    w.buttons["mark"].click()
    assert not w.catalog.is_marked(1) and w.catalog.is_marked(3) and w.catalog.index == 0
    assert not w.catalog.is_marked(0)  # the hidden viewer's picture was not touched


def test_zoom_button_opens_grid_zoom_not_viewer_zoom(w):
    in_review(w)
    w.buttons["zoom"].click()
    assert w.review.stack.currentIndex() == 2 and not w.viewer.zoomed
    assert not w.buttons["zoom"].isChecked()  # the lit state follows the viewer, which did not move


def test_global_controls_stay_global(w):
    seen = []
    w.copy_sheet_factory = lambda pics, default_dest=None, parent=None: seen.append(len(pics)) or None
    in_review(w)
    w.buttons["copy"].click()
    assert seen == [3]
    w.sidebar._rows[1][1].click()  # Marked while in review: stays
    assert w.stack.currentIndex() == 1
    w.sidebar._rows[0][1].click()  # All: back to the viewer
    assert w.stack.currentIndex() == 0
    in_review(w)
    w.buttons["review"].click()
    assert w.stack.currentIndex() == 0
    in_review(w)
    w.buttons["advance"].click()  # a preference: toggles from anywhere, review stays, viewer does not move
    assert w.advance_on_mark and w.stack.currentIndex() == 1 and w.catalog.index == 0


def test_f11_shortcut_inert_in_review(w):
    in_review(w)
    for s in w.findChildren(QShortcut):
        s.activated.emit()
    assert not w.is_bleed


def test_a_toggles_move_on_mark_while_the_real_grid_has_focus(w):
    in_review(w)
    target = w._review_target()
    assert target is w.review.view and target.hasFocus()
    QTest.keyClick(target, Qt.Key_A)  # the list view would swallow a printable key; the window shortcut wins
    assert w.advance_on_mark and w.buttons["advance"].isChecked()
    assert w.stack.currentIndex() == 1 and w.catalog.index == 0
    QTest.keyClick(target, Qt.Key_A)
    assert not w.advance_on_mark


def test_filmstrip_pick_selects_in_grid_when_marked_else_ignored(w):
    in_review(w)
    w.filmstrip.picked.emit(3)  # marked: the grid selects it, review stays, viewer picture unchanged
    assert w.stack.currentIndex() == 1 and w.review.selected_index() == 3 and w.catalog.index == 0
    assert w.review.view.currentIndex().row() == 1  # rows are [1, 3, 5]: the right ROW is current
    w.filmstrip.picked.emit(4)  # not marked: nothing to select
    assert w.stack.currentIndex() == 1 and w.review.selected_index() == 3 and w.catalog.index == 0


def test_after_review_closes_every_control_drives_the_viewer(w):
    in_review(w)
    w.review.closed.emit()
    assert all(b.isEnabled() for b in w.buttons.values())
    w.buttons["next"].click()
    assert w.catalog.index == 1
    w.buttons["back"].click()
    assert w.catalog.index == 0
    w.buttons["mark"].click()
    assert w.catalog.is_marked(0)
    w.buttons["zoom"].click()
    assert w.viewer.zoomed
    w.buttons["fullscreen"].click()
    assert w.is_bleed
    QTest.keyClick(w, Qt.Key_Escape)
    assert not w.is_bleed


def test_mark_button_is_lit_for_what_is_shown(w):
    mark = w.buttons["mark"]
    assert not mark.isChecked()  # viewer picture 0 is unmarked
    in_review(w)
    assert mark.isEnabled() and mark.isChecked()  # grid selection 1 is marked
    w.buttons["next"].click()
    assert mark.isChecked()  # every grid row is marked
    w.buttons["zoom"].click()  # the grid's zoom page: same rule
    assert w.review.stack.currentIndex() == 2 and mark.isEnabled() and mark.isChecked()
    mark.click()  # unmarks the zoomed picture
    assert mark.isChecked() == (w.review.selected_index() is not None)
    w.review.closed.emit()
    assert mark.isEnabled() and not mark.isChecked()  # follows viewer picture 0 again


def test_mark_button_disabled_and_unlit_with_no_selection(w):
    for i in (1, 3, 5):
        w.catalog.set_mark(i, False)
    w.refresh_marks()
    w.buttons["review"].click()  # the empty page
    mark = w.buttons["mark"]
    assert w.review.selected_index() is None
    assert not mark.isEnabled() and not mark.isChecked()
    w.review.closed.emit()
    assert mark.isEnabled()


def test_grid_unmark_button_updates_window(w):
    in_review(w)
    w.buttons["next"].click()  # grid row 1 = picture 3
    assert w.review.selected_index() == 3
    w.review.unmark_button.click()
    assert not w.catalog.is_marked(3) and w.catalog.index == 0
    assert w.lbl_marked.text().startswith("2 marked")
    assert w.sidebar.counts()["Marked"] == 2
    mark = w.buttons["mark"]
    assert w.review.selected_index() == 5 and mark.isEnabled() and mark.isChecked()
    for _ in range(2):  # unmark the rest: the empty page leaves nothing to act on
        w.review.unmark_button.click()
    assert w.review.selected_index() is None
    assert w.catalog.marked_count == 0 and w.lbl_marked.text().startswith("0 marked")
    assert not mark.isEnabled() and not mark.isChecked()


def test_grid_zoom_button_opens_grid_zoom_not_viewer_zoom(w):
    in_review(w)
    w.buttons["next"].click()
    w.review.zoom_button.click()
    assert w.review.stack.currentIndex() == 2
    assert not w.viewer.zoomed and not w.buttons["zoom"].isChecked()
