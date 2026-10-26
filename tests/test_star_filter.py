"""Spuza 0.17.0: the sidebar's STARS rows filter the main browsing. A threshold hides the filmstrip cells below it, and
J/K, the arrows, the no-band fast path and the advance after a mark or a star walk only the visible ones. The counts
read "shown of total". It is a view: marks and Copy never read it. Not saved between runs.

Offscreen, a real MainWindow on six synthetic pictures rated 3, 4, 0, 5, 2, 4. No clock: every wait is a pump on a condition.
"""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from spuza import features
from test_friend_r3_window import CTRL, make_window, pump

RATINGS = {0: 3, 1: 4, 2: 0, 3: 5, 4: 2, 5: 4}
NONE = Qt.KeyboardModifier.NoModifier
DIGITS = {n: getattr(Qt, f"Key_{n}") for n in range(10)}
CROP1 = (0.1, 0.2, 0.6, 0.7)


@pytest.fixture
def w():
    features.set_mode(features.ADVANCED)
    win = make_window(marked=(0, 2, 3))
    orig = win.prefetcher.info
    win.prefetcher.info = lambda i: (dataclasses.replace(got, picture_size=(6000, 4000))
                                     if (got := orig(i)) is not None else None)
    rate(win, RATINGS)
    yield win
    win.close()


def rate(win, stars: dict) -> None:
    pics = [win.catalog.pictures[i] for i in stars]
    for pic, n in zip(pics, stars.values()):
        win.pill_index.set_stars(pic, n)
    win._stars_changed(pics)  # what a key does after storing: the touched set, the counts, the strip
    win.refresh_marks()


def click_row(win, n: int) -> None:
    row = win.sidebar.star_row(n)
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(row.width() // 2, row.height() // 2))


def idx(win, did=False):
    return (win.catalog.index, win._cur_id) if did else win.catalog.index


def cells(win):
    return list(win.filmstrip.rows())


def key(win, k, mod=NONE):
    win.setFocus()
    QTest.keyClick(win, k, mod)


def crop_it(win, i=0):
    win.go_to(i)
    pump(lambda: win.prefetcher.cached(win.catalog.index) is not None and win.viewer.has_image)
    key(win, Qt.Key_X)
    win._crop_overlay.set_rect_frac(CROP1)
    key(win, Qt.Key_Return)
    return win.band.get(win._cur_id)


# -- hiding: rows and counts ---------------------------------------------------------------------------------
def test_a_row_hides_the_cells_below_the_threshold(w):
    assert [r[0] for r in cells(w)] == [0, 1, 2, 3, 4, 5]
    click_row(w, 3)
    assert [r[0] for r in cells(w)] == [0, 1, 3, 5]
    assert w.filmstrip.model().rowCount() == 4
    assert w.filmstrip.hidden_count() == 2
    click_row(w, 5)  # one threshold at a time: it replaces, it does not stack
    assert [r[0] for r in cells(w)] == [3] and w.sidebar.min_stars() == 5


def test_the_rows_are_the_five_labels_in_the_mockup_order(w):
    assert [w.sidebar.star_row(n).text() for n in (5, 4, 3, 2, 1)] == [
        "★★★★★", "★★★★ and up", "★★★ and up", "★★ and up", "★ and up"]
    assert w.sidebar.stars_head.text() == "STARS"


def test_counts_per_row_and_the_picked_row_reads_shown_of_total(w):
    assert w.sidebar.star_count_texts() == {5: "1", 4: "3", 3: "4", 2: "5", 1: "5"}
    click_row(w, 3)
    assert w.sidebar.star_count_texts() == {5: "1", 4: "3", 3: "4 of 6", 2: "5", 1: "5"}
    assert w.sidebar.count_texts()["All"] == "4 of 6"
    assert w.sidebar.count_texts()["Marked"] == "2 of 3"
    assert bool(w.sidebar.star_row(3).property("lit")) and not w.sidebar.star_row(4).property("lit")
    assert w.sidebar.star_row(3).label.font().bold() and not w.sidebar.star_row(4).label.font().bold()


def test_the_position_reads_the_visible_cells(w):
    click_row(w, 3)
    w.go_to(1)
    assert w.lbl_pos.text() == "2 of 4"
    assert w.pill.position_text() == "2 / 4"


def test_picking_the_row_again_or_all_clears_the_filter(w):
    click_row(w, 4)
    assert [r[0] for r in cells(w)] == [1, 3, 5]
    click_row(w, 4)
    assert w.sidebar.min_stars() == 0 and w._min_stars == 0 and len(cells(w)) == 6
    assert w.sidebar.count_texts()["All"] == "6"
    click_row(w, 2)
    assert len(cells(w)) == 5
    w.sidebar.show_all.emit()  # the All row
    assert w.sidebar.min_stars() == 0 and len(cells(w)) == 6 and w.sidebar.star_count_texts()[2] == "5"


def test_the_filter_says_so_in_the_status_bar(w):
    click_row(w, 3)
    assert w.last_message.startswith("Showing 4 of 6 pictures with 3★ or more")
    assert "hidden" in w.last_message


# -- navigation ----------------------------------------------------------------------------------------------
def test_jk_and_the_arrows_walk_only_visible_pictures_without_a_band(w):
    assert not w.band
    click_row(w, 3)
    assert idx(w) == 0
    seen = []
    for _ in range(5):
        key(w, Qt.Key_K)
        seen.append(idx(w))
    assert seen == [1, 3, 5, 5, 5]  # 2 and 4 are hidden; the last visible one stays
    back = []
    for _ in range(4):
        key(w, Qt.Key_J)
        back.append(idx(w))
    assert back == [3, 1, 0, 0]
    key(w, Qt.Key_Right)
    assert idx(w) == 1


def test_stepping_walks_visible_cells_with_a_band_too(w):
    d = crop_it(w, 1)
    d_id = d.id
    w.band.set_stars(d_id, 4)
    w._derived_marks_changed(d)
    click_row(w, 3)
    assert cells(w) == [(0, None), (1, None), (1, d_id), (3, None), (5, None)]
    w.go_to(0)
    got = []
    for _ in range(5):
        w.step(1)
        got.append((idx(w), w._cur_id))
    assert got == [(1, None), (1, d_id), (3, None), (5, None), (5, None)]


def test_the_band_row_positions_are_filtered_too(w):
    d = crop_it(w, 0)
    click_row(w, 1)  # the crop has no stars of its own: hidden
    assert (0, d.id) not in cells(w)
    assert w.filmstrip.row_pos(0, d.id) is None
    assert w.filmstrip.row_pos(3) == 2  # P0, P1, P3 ... P2 (0 stars) hidden
    assert w.filmstrip.row_pos(2) is None


def test_clicking_a_set_hides_the_picture_on_screen_and_the_viewer_moves_to_the_next_visible(w):
    w.go_to(2)  # 0 stars
    click_row(w, 3)
    assert idx(w) == 3  # the next visible after 2, not the first


def test_clicking_a_set_moves_back_when_nothing_follows(w):
    w.go_to(4)  # 2 stars; 5 follows and is visible at 3
    click_row(w, 5)  # only picture 3 is visible: it is before 4
    assert idx(w) == 3


# -- a star change that drops the picture on screen --------------------------------------------------------
def test_a_star_that_drops_the_current_picture_lands_on_the_next_visible_without_skipping(w):
    click_row(w, 3)
    w.go_to(1)
    key(w, DIGITS[1], CTRL)  # 1 star: below 3. One step, to 3 (not 5)
    assert idx(w) == 3
    assert [r[0] for r in cells(w)] == [0, 3, 5]


def test_a_star_that_keeps_the_picture_still_advances_one_visible_picture(w):
    click_row(w, 3)
    w.go_to(0)
    key(w, DIGITS[5], CTRL)
    assert idx(w) == 1


def test_clearing_the_stars_of_the_current_picture_moves_to_the_next_visible_once(w):
    click_row(w, 3)
    w.go_to(1)
    key(w, DIGITS[0], CTRL)  # a clear never advances by itself; the picture is hidden now, so it settles
    assert idx(w) == 3


def test_dropping_the_last_visible_picture_lands_on_the_previous_one(w):
    click_row(w, 3)
    w.go_to(5)
    key(w, DIGITS[2], CTRL)
    assert idx(w) == 3


def test_dropping_the_only_visible_picture_leaves_the_viewer_where_it_is_and_the_strip_empty(w):
    click_row(w, 5)
    w.go_to(3)
    key(w, DIGITS[1], CTRL)
    assert cells(w) == [] and w.filmstrip.model().rowCount() == 0
    assert idx(w) == 3  # nothing to move to: the picture stays on screen, it is only hidden
    assert w.lbl_pos.text() == "none with 5★ or more"
    key(w, Qt.Key_K)
    assert idx(w) == 3
    click_row(w, 5)  # off again: everything is back
    assert len(cells(w)) == 6 and w.lbl_pos.text() == "4 of 6"


def test_an_empty_filter_says_no_picture_has_that_many(w):
    rate(w, {3: 4})
    click_row(w, 5)
    assert cells(w) == []
    assert w.last_message.startswith("No picture has 5★ or more")


# -- crops and stories follow their own stars ----------------------------------------------------------------
def test_a_new_crop_starts_unrated_so_a_one_star_filter_hides_it(w):
    click_row(w, 1)
    d = crop_it(w, 3)  # the source has 5 stars; the crop has none and no tags
    assert d.stars in (None, 0) and not d.pills
    assert (3, d.id) not in cells(w) and (3, None) in cells(w)
    assert w.sidebar.count_texts()["All"] == "5 of 7"  # the crop is a cell, counted in the total
    w.band.set_stars(d.id, 2)
    w._derived_marks_changed(d)
    assert (3, d.id) in cells(w)


def test_a_rated_crop_shows_even_when_its_source_is_hidden(w):
    d = crop_it(w, 2)  # P2 has 0 stars
    w.band.set_stars(d.id, 4)
    w._derived_marks_changed(d)
    click_row(w, 3)
    assert (2, None) not in cells(w) and (2, d.id) in cells(w)


def test_removing_a_crop_under_the_filter_lands_on_a_visible_picture(w):
    d = crop_it(w, 2)  # P2 has 0 stars: hidden under the filter; its crop is rated and shown
    w.band.set_stars(d.id, 1)
    w._derived_marks_changed(d)
    click_row(w, 1)
    assert w._cur_id == d.id and (2, None) not in cells(w)
    key(w, Qt.Key_Backspace)
    assert not w.band
    assert (idx(w), w._cur_id) in cells(w) and idx(w) != 2


def test_review_lists_originals_only_and_agrees_with_the_filter(w):
    crop_it(w, 0)
    for i in range(6):
        w.catalog.set_mark(i, True)
    click_row(w, 4)
    w.open_review()
    assert w.review.set_indices() == [r[0] for r in cells(w) if r[1] is None] == [1, 3, 5]
    assert w.review.min_stars == 4


# -- a view only ----------------------------------------------------------------------------------------------
def test_the_filter_never_changes_marks(w):
    before = sorted(i for i in range(6) if w.catalog.is_marked(i))
    click_row(w, 5)
    click_row(w, 5)
    click_row(w, 3)
    assert sorted(i for i in range(6) if w.catalog.is_marked(i)) == before == [0, 2, 3]
    assert w._marked_total() == 3
    assert w.sidebar.count_texts()["Marked"] == "2 of 3"


def test_copy_gets_exactly_the_same_pictures_with_and_without_the_filter(w):
    got = {}

    class Sheet:
        def __init__(self, **kw):
            pass

        def show(self):
            pass

    def factory(pictures, **kw):
        got["pictures"], got["kw"] = list(pictures), kw
        return Sheet()

    w.copy_sheet_factory = factory
    w.open_copy()
    plain = dict(got)
    got.clear()
    click_row(w, 4)
    assert w.catalog.is_marked(2) and (2, None) not in cells(w)  # a marked picture the filter hides
    w.open_copy()
    assert got["pictures"] == plain["pictures"] and got["kw"] == plain["kw"]
    assert [p.stem for p in got["pictures"]] == ["P0", "P2", "P3"]


# -- persistence: not saved ---------------------------------------------------------------------------------
def test_the_filter_is_not_saved_a_new_window_starts_unfiltered(w):
    click_row(w, 4)
    assert w.prefs.value("min_stars") is None
    w.close()
    again = make_window(marked=())
    try:
        assert again._min_stars == 0 and again.sidebar.min_stars() == 0
        assert [r[0] for r in again.filmstrip.rows()] == [0, 1, 2, 3, 4, 5]
        assert again.sidebar.count_texts()["All"] == "6"
    finally:
        again.close()


def test_a_threshold_survives_a_readback_restart_and_refills_as_the_stars_arrive(w):
    click_row(w, 4)
    w.start_pill_readback()
    w._pill_gen += 1  # hold the real job's answers back: nothing is known yet
    w._pill_ready = False
    w._stars_touched.clear()
    w._file_stars.clear()
    w._star_counts = None
    w.filmstrip.refilter()
    assert cells(w) == []
    w._on_pill_batch(w._pill_gen, [(1, None, 4), (3, None, 5)])
    assert [r[0] for r in cells(w)] == [1, 3]


# -- the filmstrip on its own -------------------------------------------------------------------------------
def test_the_filmstrip_filters_by_its_own_star_provider(w):
    s = w.filmstrip
    s.stars_of = lambda i: i  # 0..5
    s.set_min_stars(4)
    assert [r[0] for r in s.rows()] == [4, 5]
    assert s.row_pos(5) == 1 and s.row_pos(0) is None
    s.item_changed(5)  # a span of a visible original
    s.item_changed(0)  # none: must not raise
    s.stars_of = lambda i: 5 if i == 0 else 0
    assert s.refilter() is True and [r[0] for r in s.rows()] == [0]
    assert s.refilter() is False
    s.set_min_stars(0)
    assert len(s.rows()) == 6 and s.hidden_count() == 0

