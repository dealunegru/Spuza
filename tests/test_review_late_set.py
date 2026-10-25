"""A review opened on a set that is still empty (the star filter while the stars are being read back) lands on the
zoom page as soon as the set fills, unless the user has navigated first. The read-back is held back by hand: no
thread timing is involved. Synthetic pictures only."""

from __future__ import annotations

import pytest

from spuza import features
from test_friend_r3_window import make_window


@pytest.fixture
def held():
    """A window whose star read-back "has not finished": the real job's results are ignored, and the stars the index
    holds are not yet visible (the window answers 0 for a picture it was not told about)."""
    features.set_mode(features.ADVANCED)
    win = make_window()
    win._pill_gen += 1  # the real job's batches and finish are now stale
    win._pill_ready = False
    for i in (1, 3, 5):
        win.pill_index.set_stars(win.catalog.pictures[i], 3)
    win._set_min_stars(3)
    win.open_review()
    yield win
    win.close()


def fill(win):
    """Deliver the read-back's star answers the way the worker does."""
    win._on_pill_batch(win._pill_gen, [(i, None, 3) for i in (1, 3, 5)])


def test_the_set_is_empty_at_open(held):
    r = held.review
    assert r._members == [] and r.stack.currentIndex() == 0


def test_a_set_that_fills_later_lands_on_the_zoom_page_at_the_first_picture(held):
    fill(held)
    r = held.review
    assert r._members == [1, 3, 5]
    assert r.stack.currentIndex() == 2 and r._zoom_index == 1


def test_the_preferred_picture_wins_when_it_is_in_the_set(held):
    r = held.review
    r.open_set("marked", None, prefer=3)
    fill(held)
    assert r.stack.currentIndex() == 2 and r._zoom_index == 3


def test_a_user_who_went_to_the_grid_stays_there(held):
    r = held.review
    r.leave_zoom()
    fill(held)
    assert r._members == [1, 3, 5] and r.stack.currentIndex() == 1 and r._zoom_index is None


def test_a_set_that_never_fills_stays_empty_and_a_later_refresh_does_not_zoom_after_navigation(held):
    r = held.review
    r.refresh()
    assert r.stack.currentIndex() == 0 and r._zoom_index is None
    r.set_source("marked")  # another open of the review: the old pending zoom is gone
    fill(held)
    assert r.stack.currentIndex() == 1 and r._zoom_index is None


def test_a_zoom_chosen_by_the_user_is_not_replaced(held):
    r = held.review
    fill(held)
    r.show_zoom(5)
    held.review.refresh()
    assert r._zoom_index == 5
