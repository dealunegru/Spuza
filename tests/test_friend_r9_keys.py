"""Friend round 9: adding a pill or 1..5 stars always shows the next picture; Start fresh has no key; Help > About Spuza.
Synthetic pictures only (offscreen)."""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest

import spuza
from spuza import features
from spuza.ui import actions as act
from test_friend_r3_window import CTRL, make_window, pump

NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "spuza-logs"))
    monkeypatch.setenv("SPUZA_SESSIONS_DIR", str(tmp_path / "env-sessions"))
    monkeypatch.setenv("SPUZA_PILLS_DIR", str(tmp_path / "pills"))


@pytest.fixture
def w():
    win = make_window(n=5, marked=())
    orig = win.prefetcher.info
    win.prefetcher.info = lambda i: (lambda g: dataclasses.replace(g, picture_size=(6000, 4000)) if g else g)(orig(i))
    win.pill_book.create("Hero", "Blue")
    win.pill_book.create("Spare", "Red")
    win._pills_edited()
    assert not win.advance_on_mark  # the A toggle is off: advancing must not depend on it
    yield win
    win.close()


def key(win, k, mod=NONE):
    win.setFocus()
    QTest.keyClick(win, k, mod)


def names(win, i):
    return win.pill_index.get(win.catalog.pictures[i])


def stars(win, i):
    return win.pill_index.stars(win.catalog.pictures[i])


# ---- the viewer ----------------------------------------------------------------------------------------

def test_a_digit_tags_and_shows_the_next_picture(w):
    key(w, Qt.Key_1)
    assert names(w, 0) == ("Hero",) and w.catalog.index == 1
    assert w.pill_tray is not None and "Hero" not in w._current_pill_names()  # the tray follows the new picture


def test_stars_by_key_menu_and_bar_advance(w):
    key(w, Qt.Key_3, CTRL)
    assert stars(w, 0) == 3 and w.catalog.index == 1
    w.menu_actions["stars_4"].trigger()
    assert stars(w, 1) == 4 and w.catalog.index == 2
    w.star_bar.rated.emit(5)
    assert stars(w, 2) == 5 and w.catalog.index == 3


def test_pills_menu_and_tray_advance(w):
    w.menu_actions["pill_1"].trigger()
    assert names(w, 0) == ("Hero",) and w.catalog.index == 1
    w._tray_toggled("Spare")
    assert names(w, 1) == ("Spare",) and w.catalog.index == 2


def test_clearing_and_taking_a_pill_off_stay_put(w):
    w.pill_index.toggle(w.catalog.pictures[0], "Hero")
    w.pill_index.set_stars(w.catalog.pictures[0], 2)
    key(w, Qt.Key_1)  # off
    assert names(w, 0) == () and w.catalog.index == 0
    key(w, Qt.Key_0)
    key(w, Qt.Key_0, CTRL)
    assert stars(w, 0) == 0 and w.catalog.index == 0


def test_the_last_picture_stays(w):
    w.go_to(4)
    key(w, Qt.Key_1)
    assert names(w, 4) == ("Hero",) and w.catalog.index == 4
    key(w, Qt.Key_2, CTRL)
    assert stars(w, 4) == 2 and w.catalog.index == 4


def test_the_pill_manager_path_does_not_advance(w):
    w._pill_key(1)  # the plain handler (what the manager side uses) never moves
    w._stars_key(2)
    assert w.catalog.index == 0


def test_a_derived_row_is_marked_then_the_next_row_shows(w):
    from spuza.derived import CROP

    d = w.band.add(0, CROP, crop=(0.1, 0.2, 0.6, 0.7))
    w._band_changed()
    w.go_to(0, d.id)
    pump(lambda: w._cur_id == d.id)
    key(w, Qt.Key_1)
    assert w.band.get(d.id).pills == ("Hero",) and names(w, 0) == ()
    assert (w.catalog.index, w._cur_id) == (1, None)


# ---- review -------------------------------------------------------------------------------------------

@pytest.fixture
def rv(w):
    features.set_mode(features.ADVANCED)
    for i in (0, 1, 2):
        w.catalog.toggle_mark(i)
    w.open_review()
    pump(lambda: w.stack.currentIndex() == 1)
    return w


def test_review_zoom_page_steps_the_set_without_wrapping(rv):
    r = rv.review
    assert r.stack.currentIndex() == 2 and r._zoom_index == 0
    key(rv, Qt.Key_1)
    assert names(rv, 0) == ("Hero",) and r._zoom_index == 1 and rv.catalog.index == 0
    key(rv, Qt.Key_3, CTRL)
    assert stars(rv, 1) == 3 and r._zoom_index == 2
    key(rv, Qt.Key_2)
    assert names(rv, 2) == ("Spare",) and r._zoom_index == 2  # last: stays
    key(rv, Qt.Key_0)  # clear: stays
    assert r._zoom_index == 2


def test_review_grid_selection_does_not_advance(rv):
    r = rv.review
    r.leave_zoom()
    r.view.setCurrentIndex(r.model.index(0, 0))
    before = r.selected_index()
    QTest.keyClick(r.view, Qt.Key_1)
    assert names(rv, before) == ("Hero",) and r.selected_index() == before and rv.catalog.index == 0


# ---- U and About --------------------------------------------------------------------------------------

def test_u_does_nothing_and_start_fresh_stays_in_the_menu(w):
    features.set_mode(features.ADVANCED)
    w.toggle_mark()
    w.go_to(0)
    key(w, Qt.Key_U)
    assert w.catalog.is_marked(0)
    a = act.by_id()["start_fresh"]
    assert a.keys == () and a.menu == "Edit" and "start_fresh" in w.menu_actions


def test_about_is_in_help_with_the_about_role(w):
    a = act.by_id()["about"]
    assert (a.menu, a.label, a.keys, a.needs_picture) == ("Help", "About Spuza", (), False)
    assert w.menu_actions["about"].menuRole() == QAction.MenuRole.AboutRole
    others = {i: q.menuRole() for i, q in w.menu_actions.items() if i != "about"}
    assert QAction.MenuRole.AboutRole not in others.values()
    t = w.about_text()
    assert "Spuza" in t and spuza.__version__ in t and "https://github.com/dealunegru/Spuza" in t


def test_act_about_shows_the_text(w, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    seen = []
    monkeypatch.setattr(QMessageBox, "about", lambda parent, title, text: seen.append((title, text)))
    w.menu_actions["about"].trigger()
    assert seen == [("About Spuza", w.about_text())]


def test_the_overlay_lists_no_keyless_action(w):
    from PySide6.QtWidgets import QLabel

    from spuza.ui import shortcut_overlay as so

    w.toggle_shortcuts()
    ov = w._shortcuts
    labels = ov.findChildren(QLabel)
    keys = [q.text() for q in labels if q.property("role") == "key"]
    texts = {q.text() for q in labels}
    assert keys and all(k.strip() for k in keys)
    assert "Start fresh" not in texts and "About Spuza" not in texts
    assert all(k.strip() for _g, _l, k in so.rows())
    ov.close_overlay()


# ---- the star filter: a rating can take the picture out of the review set ------------------------------

@pytest.fixture
def filtered(w):
    features.set_mode(features.ADVANCED)
    for i in (0, 1, 2):
        w.catalog.toggle_mark(i)
        w.pill_index.set_stars(w.catalog.pictures[i], 3)
    w._set_min_stars(3)
    w.open_review()
    pump(lambda: w.stack.currentIndex() == 1)
    pump(lambda: w.review._members == [0, 1, 2])
    assert w.review.stack.currentIndex() == 2 and w.review._members == [0, 1, 2]
    return w


def test_a_rating_that_drops_the_picture_lands_on_the_immediate_next(filtered):
    r = filtered.review
    key(filtered, Qt.Key_1, CTRL)  # 1 star < 3: picture 0 leaves the set; review moved to 1 by itself
    assert stars(filtered, 0) == 1 and r._members == [1, 2] and r._zoom_index == 1


def test_a_rating_that_drops_the_last_picture_does_not_skip_or_complain(filtered):
    r = filtered.review
    r.show_zoom(2)
    key(filtered, Qt.Key_1, CTRL)
    assert r._members == [0, 1] and r._zoom_index == 1
    assert "last picture" not in filtered.last_message


def test_a_rating_that_keeps_the_picture_advances_by_exactly_one(filtered):
    r = filtered.review
    key(filtered, Qt.Key_4, CTRL)
    assert r._members == [0, 1, 2] and r._zoom_index == 1
