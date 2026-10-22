"""Star ratings in the window (Spuza 0.14.0, improvement-446, dec-055..058): Ctrl/Cmd+1..5 and Ctrl/Cmd+0, the Stars
menu and `?` group, the Quiet bar's StarBar, ratings read back from files, the "at least N" review filter (F2), the
review grid's stars, and ratings handed to Copy.

Offscreen QApplication, real MainWindow on tiny synthetic JPEGs in tmp_path. conftest points SPUZA_PILLS_DIR at a
per-test folder, so nothing reaches the real app data.
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

from spuza import features, xmp
from spuza.catalog import Catalog, Prefetcher, scan
from spuza.ui import actions as act
from spuza.ui import theme
from spuza.ui.pill_tray import native
from spuza.ui.window import MainWindow

APP = QApplication.instance() or QApplication([])
CTRL = Qt.KeyboardModifier.ControlModifier
KEYS = {n: getattr(Qt, f"Key_{n}") for n in range(10)}


@pytest.fixture(autouse=True)
def isolated_theme():
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


def make_pictures(folder: Path, n: int = 5, ratings: dict | None = None) -> None:
    """n tiny JPEGs P0.jpg..; `ratings` maps an index to the xmp:Rating another app embedded in that file."""
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        path = folder / f"P{i}.jpg"
        Image.new("RGB", (64, 48), (40 * (i % 5) + 30, 90, 160)).save(path)
        if ratings and i in ratings:
            want = ratings[i]
            new, why = xmp.embed(path.read_bytes(), ".jpg", [], 5 if want < 0 else want)
            assert new is not None, why
            if want < 0:  # another app's reject: Spuza itself never writes -1, so patch the packet by hand
                assert b"<xmp:Rating>5</xmp:Rating>" in new
                new = new.replace(b"<xmp:Rating>5</xmp:Rating>", b"<xmp:Rating>-1</xmp:Rating>")
            path.write_bytes(new)


def make_window(folder: Path, n: int = 5, ratings=None, size=(1200, 800)) -> MainWindow:
    make_pictures(folder, n, ratings)
    cat = Catalog(scan(folder))
    w = MainWindow(cat, Prefetcher(cat), folder)
    w.resize(*size)
    w.show()
    pump(lambda: w.viewer.has_image)
    assert w.wait_pill_readback()
    return w


@pytest.fixture
def win(tmp_path):
    w = make_window(tmp_path / "card")
    yield w
    w.close()


def pic(w, i=0):
    return w.catalog.pictures[i]


def stars_in_index(w, i=0):
    return w.pill_index.stars(pic(w, i))


def press(w, n, mods=CTRL):
    w.setFocus()
    QTest.keyClick(w, KEYS[n], mods)


# -- keys in the viewer ---------------------------------------------------------------------------------
def test_ctrl_4_sets_four_stars_everywhere(win):
    w = win
    press(w, 4)
    assert stars_in_index(w) == 4
    assert w.inspector.stars == 4 and "4 of 5" in w.inspector.stars_line()
    assert w.pill.stars_text() == "★★★★"
    assert w.star_bar.stars() == 4
    assert w.filmstrip.star_count(0) == 4
    assert w.last_message == f"★★★★ 4 stars · {native('Ctrl+4')}"
    assert stars_in_index(w, 1) is None  # only this picture


def test_ctrl_0_clears_to_zero_not_none(win):
    w = win
    press(w, 2)
    press(w, 0)
    assert stars_in_index(w) == 0
    assert w.inspector.stars == 0 and w.star_bar.stars() == 0 and w.pill.stars_text() == ""
    assert w.last_message == f"Stars cleared · {native('Ctrl+0')}"


def test_plain_digits_still_toggle_pills_and_leave_stars(win):
    w = win
    w.pill_book.create("Hero", "Blue")
    w._pills_edited()
    press(w, 1, Qt.KeyboardModifier.NoModifier)
    assert w.pill_index.get(pic(w)) == ("Hero",)
    assert stars_in_index(w) is None


def test_keys_are_ignored_in_crop_and_story(win):
    w = win
    for start in (w.start_crop, w.start_story):
        start()
        assert w._edit is not None
        press(w, 3)
        assert stars_in_index(w) is None
        w._end_edit()
    press(w, 3)
    assert stars_in_index(w) == 3  # and back to working once the edit is closed


def test_star_actions_are_disabled_in_an_edit(win):
    w = win
    w.start_crop()
    w.refresh_actions()
    assert not w.menu_actions["stars_3"].isEnabled()
    w._end_edit()
    w.refresh_actions()
    assert w.menu_actions["stars_3"].isEnabled()


@pytest.mark.parametrize("n", range(0, 6))
def test_each_star_key_fires_its_handler_exactly_once(win, monkeypatch, n):
    calls = []
    monkeypatch.setattr(win, "_stars_key", lambda k: calls.append(k))
    press(win, n)
    assert calls == [n]  # the window shortcut, not also the menu action's own


def test_menu_action_runs_the_same_handler(win):
    win.menu_actions["stars_5"].trigger()
    assert stars_in_index(win) == 5


# -- the table, the menu and the ? overlay ------------------------------------------------------------------
def test_action_table_has_the_stars_rows():
    by = act.by_id()
    for n in range(1, 6):
        a = by[f"stars_{n}"]
        assert a.keys == (f"Ctrl+{n}",) and a.menu == "Stars" and a.group == "Stars"
        assert a.handler == f"act_stars_{n}"
    assert by["stars_clear"].keys == ("Ctrl+0",)
    assert "Stars" in act.MENUS and "Stars" in act.GROUPS


def test_stars_menu_and_overlay_list_the_group(win):
    w = win
    assert [a.text() for a in w.menuBar().actions()] == ["File", "Edit", "View", "Picture", "Pills", "Stars", "Help"]
    menu = [a for a in w.menuBar().actions() if a.text() == "Stars"][0].menu()
    assert [a.text() for a in menu.actions()] == ["1 star", "2 stars", "3 stars", "4 stars", "5 stars", "Clear stars"]
    w.toggle_shortcuts()
    labels = w._shortcuts.labels()
    assert "STARS" in labels and "Clear stars" in labels and "5 stars" in labels
    assert native("Ctrl+3") in labels


# -- review: multi-selection and keys through the grid ---------------------------------------------------
def open_marked_review(w, marks=(0, 1, 2, 3, 4)):
    for i in marks:
        w.catalog.set_mark(i, True)
    w.refresh_marks()
    w.open_review()
    return w.review


def test_ctrl_3_in_review_sets_every_selected_picture(tmp_path):
    was = features.mode()
    features.set_mode(features.ADVANCED)  # multi-select in the grid needs advanced + compare_view
    w = make_window(tmp_path / "card")
    try:
        r = open_marked_review(w)
        r.leave_zoom()
        sel = r.view.selectionModel()
        for row in (1, 3):
            from PySide6.QtCore import QItemSelectionModel

            sel.select(r.model.index(row, 0), QItemSelectionModel.SelectionFlag.Select)
        assert set(r.selected_indices()) >= {1, 3}
        picks = r.selected_indices()
        r.view.setFocus()
        QTest.keyClick(r.view, KEYS[3], CTRL)  # the grid ignores Ctrl+digit; the window shortcut takes it
        assert all(stars_in_index(w, i) == 3 for i in picks)
        assert len(picks) >= 2 and f"{len(picks)} pictures" in w.last_message
    finally:
        w.close()
        features.set_mode(was)


def test_ctrl_digit_reaches_the_window_in_review_on_the_zoom_page(win):
    w = win
    r = open_marked_review(w)
    r.show_zoom(2)
    r.zoom_view.setFocus()
    QTest.keyClick(r.zoom_view, KEYS[5], CTRL)
    assert stars_in_index(w, 2) == 5
    assert stars_in_index(w, 0) is None


def test_plain_digit_in_review_is_still_a_pill_key(win):
    w = win
    w.pill_book.create("Hero", "Blue")
    w._pills_edited()
    r = open_marked_review(w)
    r.show_zoom(1)
    r.zoom_view.setFocus()
    QTest.keyClick(r.zoom_view, KEYS[1])
    assert w.pill_index.get(pic(w, 1)) == ("Hero",)
    assert stars_in_index(w, 1) is None


# -- the Quiet bar's StarBar --------------------------------------------------------------------------------
def click_star(bar, n):
    r = bar.star_rect(n)
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(int(r.center().x()), int(r.center().y())))


def test_star_bar_sits_after_the_tray_and_shows_the_current_picture(win):
    w = win
    lay = w.quiet_bar._lay
    items = [lay.itemAt(k).widget() for k in range(lay.count())]
    tray_at = items.index(w.pill_tray)
    assert w.star_bar in items[tray_at + 1].findChildren(type(w.star_bar)) or items[tray_at + 1] is w.star_bar
    assert w.quiet_bar.star_bar_shown()
    press(w, 2)
    assert w.star_bar.stars() == 2
    w.step(1)
    assert w.star_bar.stars() == 0
    w.step(-1)
    assert w.star_bar.stars() == 2


def test_star_bar_click_sets_and_the_lit_top_star_clears(win):
    w = win
    click_star(w.star_bar, 3)
    assert stars_in_index(w) == 3 and w.inspector.stars == 3
    click_star(w.star_bar, 3)
    assert stars_in_index(w) == 0 and w.star_bar.stars() == 0
    click_star(w.star_bar, 5)
    assert stars_in_index(w) == 5


def test_star_bar_gives_way_and_copy_stays_inside_on_narrow_windows(tmp_path):
    w = make_window(tmp_path / "card", size=(1400, 800))
    try:
        assert w.quiet_bar.star_bar_shown()
        shown_at = {}
        for width in (1400, 1000, 800, 640, 520):
            w.resize(width, 800)
            QApplication.processEvents()
            QApplication.processEvents()
            bar = w.quiet_bar
            copy = w.buttons["copy"]
            right = copy.mapTo(bar, QPoint(copy.width(), 0)).x()
            assert right <= bar.width(), (width, right, bar.width())
            shown_at[width] = bar.star_bar_shown()
        assert shown_at[1400] is True
        assert shown_at[520] is False  # it gave way
    finally:
        w.close()


# -- read-back of file ratings ----------------------------------------------------------------------------------
def test_a_foreign_rating_in_the_file_shows_after_load(tmp_path):
    w = make_window(tmp_path / "card", ratings={0: 3, 1: -1, 2: 5})
    try:
        assert w._stars_of(0) == 3 and w.inspector.stars == 3 and w.star_bar.stars() == 3
        assert w._stars_of(1) == 0  # a -1 reject shows as no stars
        assert w._stars_of(2) == 5 and w.filmstrip.star_count(2) == 5
        assert w._stars_of(3) == 0
        assert stars_in_index(w, 0) is None  # shown, not copied into the index
    finally:
        w.close()


def test_the_index_value_overrides_the_file_rating(tmp_path):
    w = make_window(tmp_path / "card", ratings={0: 3})
    try:
        press(w, 1)
        assert w.star_bar.stars() == 1 and w._stars_of(0) == 1
        press(w, 0)
        assert w._stars_of(0) == 0  # cleared in Spuza beats the file's 3
    finally:
        w.close()


def test_the_batch_handler_accepts_old_pairs_and_new_triples(win):
    w = win
    w._on_pill_batch(w._pill_gen, [(1, None, 4), (2, [xmp.PillTag("Ghost", "#ff4d4d")])])
    assert w._stars_of(1) == 4
    assert w.pill_index.get(pic(w, 2)) == ("Ghost",)
    w._on_pill_batch(w._pill_gen - 1, [(3, None, 5)])  # an older read arrives late
    assert w._stars_of(3) == 0


def test_the_readback_reads_pills_and_rating_in_one_pass(tmp_path, monkeypatch):
    calls = []
    real = xmp.read_picture_meta

    def spy(p):
        calls.append(p.primary.name)
        return real(p)

    monkeypatch.setattr(xmp, "read_picture_meta", spy)
    w = make_window(tmp_path / "card", n=3, ratings={0: 2})
    try:
        assert sorted(calls) == ["P0.jpg", "P1.jpg", "P2.jpg"]
        assert w._stars_of(0) == 2
    finally:
        w.close()


# -- F2: the "at least N" threshold --------------------------------------------------------------------------
def rate(w, stars: dict):
    for i, n in stars.items():
        w.pill_index.set_stars(pic(w, i), n)
        w._stars_touched.add(pic(w, i).primary)
    w._star_counts = None
    w.refresh_marks()


def click_chip(w, n):
    chip = w.sidebar.star_chip(n)
    QTest.mouseClick(chip, Qt.MouseButton.LeftButton, pos=QPoint(chip.width() // 2, chip.height() // 2))


def test_chip_row_lights_one_and_clicking_it_again_clears(win):
    w = win
    assert [w.sidebar.star_chip(n).text() for n in range(1, 6)] == [f"{n}★" for n in range(1, 6)]
    click_chip(w, 3)
    assert w.sidebar.min_stars() == 3 and w._min_stars == 3
    click_chip(w, 4)
    assert w.sidebar.min_stars() == 4  # one lit at a time
    assert [bool(w.sidebar.star_chip(n).property("lit")) for n in range(1, 6)] == [False, False, False, True, False]
    click_chip(w, 4)
    assert w.sidebar.min_stars() == 0 and w._min_stars == 0


def test_counts_read_n_of_m_while_a_threshold_is_set(win):
    w = win
    w.pill_book.create("Hero", "Blue")
    w._pills_edited()
    for i in (0, 1, 2):
        w.catalog.set_mark(i, True)
    for i in (0, 1):
        w.pill_index.toggle(pic(w, i), "Hero")
    w._pill_counts = None
    rate(w, {0: 4, 1: 2, 2: 5, 3: 3})
    assert w.sidebar.count_texts() == {"All": "5", "Marked": "3", "Hero": "2"}
    click_chip(w, 3)
    assert w.sidebar.count_texts() == {"All": "3 of 5", "Marked": "2 of 3", "Hero": "1 of 2"}
    assert w.sidebar.counts() == {"All": 3, "Marked": 2}
    click_chip(w, 3)
    assert w.sidebar.count_texts() == {"All": "5", "Marked": "3", "Hero": "2"}


def test_threshold_narrows_marked_and_pill_sets_as_an_and(win):
    w = win
    w.pill_book.create("Hero", "Blue")
    w._pills_edited()
    for i in (0, 1, 2, 3, 4):
        w.catalog.set_mark(i, True)
    for i in (1, 2, 3):
        w.pill_index.toggle(pic(w, i), "Hero")
    rate(w, {0: 1, 1: 3, 2: 4, 3: 5, 4: 2})
    click_chip(w, 3)
    w.open_review()
    r = w.review
    assert r.set_indices() == [1, 2, 3]
    assert r.header.text() == "Marked · 3★ and up: 3 of 5 pictures"
    w.open_pill_set("Hero")
    assert r.set_indices() == [1, 2, 3]
    w.pill_index.toggle(pic(w, 3), "Hero")  # no longer in Hero
    w._pills_changed([pic(w, 3)])
    assert r.set_indices() == [1, 2]
    assert r.header.text() == "Hero · 3★ and up: 2 of 2 pictures"
    click_chip(w, 4)
    assert r.set_indices() == [2]
    click_chip(w, 4)  # cleared: plain Hero again
    assert r.set_indices() == [1, 2]
    assert r.header.text().startswith("Hero: 2 of 5")


def test_stepping_a_filtered_set_visits_exactly_the_filtered_pictures(win):
    w = win
    for i in range(5):
        w.catalog.set_mark(i, True)
    rate(w, {0: 1, 1: 3, 2: 2, 3: 5, 4: 4})
    click_chip(w, 3)
    w.open_review()
    r = w.review
    seen = [r._zoom_index]
    for _ in range(6):
        r.step_set(1)
        if r._zoom_index != seen[-1]:
            seen.append(r._zoom_index)
    assert seen == [1, 3, 4] == r._members
    assert w.sidebar.open_row() == "Marked"


def test_a_picture_that_falls_below_the_threshold_leaves_the_open_set(win):
    w = win
    for i in (0, 1):
        w.catalog.set_mark(i, True)
    rate(w, {0: 4, 1: 4})
    click_chip(w, 3)
    w.open_review()
    r = w.review
    assert r._members == [0, 1]
    r.show_zoom(0)
    press(w, 1)  # 1 star: below the threshold
    assert r._members == [1]
    assert r._zoom_index == 1


def test_the_main_viewer_is_not_filtered(win):
    w = win
    rate(w, {0: 1, 1: 5})
    click_chip(w, 5)
    assert w.catalog.index == 0
    w.step(1)
    assert w.catalog.index == 1
    w.step(1)
    assert w.catalog.index == 2  # a 0-star picture: J/K still step it
    assert w.filmstrip.star_count(1) == 5 and w.filmstrip.star_count(0) == 1


def test_review_grid_draws_stars_and_provider_errors_show_none(win):
    w = win
    for i in (0, 1):
        w.catalog.set_mark(i, True)
    rate(w, {0: 4})
    w.open_review()
    r = w.review
    assert r.star_count(0) == 4 and r.star_count(1) == 0
    r.stars_of = lambda i: 1 / 0
    assert r.star_count(0) == 0
    r.stars_of = lambda i: "junk"
    assert r.star_count(0) == 0
    r.stars_of = w._stars_of
    r.leave_zoom()
    r.show()
    pm = r.view.viewport().grab()  # painting the delegate with stars must not raise
    assert not pm.isNull()


# -- Copy ---------------------------------------------------------------------------------------------------------
class FakeSheet:
    def __init__(self, **kw):
        self.kw = kw

    def show(self):
        pass


def copy_call(w):
    got = {}

    def factory(pictures, **kw):
        got["pictures"], got["kw"] = pictures, kw
        return FakeSheet(**kw)

    w.copy_sheet_factory = factory
    w.open_copy()
    return got


def test_copy_gets_ratings_only_when_some_are_set_in_spuza(win):
    w = win
    for i in (0, 1, 2):
        w.catalog.set_mark(i, True)
    assert "ratings" not in copy_call(w)["kw"]  # nothing rated in Spuza: the old call shape
    rate(w, {0: 4, 1: 0})
    kw = copy_call(w)["kw"]
    assert kw["ratings"] == {pic(w, 0).primary: 4, pic(w, 1).primary: 0}  # 0 = clear; picture 2 is left alone


def test_copy_ignores_a_file_rating_that_spuza_did_not_set(tmp_path):
    w = make_window(tmp_path / "card", ratings={0: 5})
    try:
        w.catalog.set_mark(0, True)
        assert "ratings" not in copy_call(w)["kw"]
    finally:
        w.close()


def test_default_factory_forwards_ratings(win, monkeypatch):
    w = win
    seen = {}

    class Sheet:
        def __init__(self, *a, **kw):
            seen.update(kw)

    import spuza.ui.copy_sheet as cs

    monkeypatch.setattr(cs, "CopySheet", Sheet)
    p = pic(w, 0)
    w._default_copy_sheet_factory([p], ratings={p.primary: 2})
    assert seen["ratings"] == {p.primary: 2}
    seen.clear()
    w._default_copy_sheet_factory([p])
    assert "ratings" not in seen


# -- saving -------------------------------------------------------------------------------------------------------
def test_stars_are_saved_with_the_pill_index_and_flushed_on_close(tmp_path):
    w = make_window(tmp_path / "card")
    press(w, 5)
    assert w._pill_dirty
    w.close()
    from spuza import pills as pills_mod

    idx = pills_mod.PillIndex.load()
    assert idx.stars(Catalog(scan(tmp_path / "card")).pictures[0]) == 5
