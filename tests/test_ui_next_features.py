"""The four advanced-only features through the real MainWindow, offscreen, with a fake loader.

Compare (N), focus peaking (P), the AF point (O and Z) and resume (U): in classic every key falls through to
today's behaviour and nothing is built, shown or written; in advanced each works end to end.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from spuza import features
from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.focus import FocusInfo
from spuza.preview import PreviewInfo
from spuza.ui import settings
from spuza.ui.window import MainWindow

ROOT = Path("/cards/X")
N = 6
W, H = 1600, 1200
FOCUS_AT = 2  # picture 2 carries a usable AF point at (0.625, 0.25) of the frame
FOCUS = FocusInfo("X-T5", "Auto", "Single Point", (1000, 300), (W, H), 1)

_BOARD = np.kron(np.indices((H // 40, W // 40)).sum(axis=0) % 2, np.ones((40, 40))) * 200
_BOARD = Image.fromarray(np.stack([_BOARD] * 3, axis=-1).astype("uint8"))


def loader(path, max_side):
    i = int(Path(path).stem[1:])
    info = PreviewInfo(path, "raf-embedded", W, H, 10, 1.0, None, FOCUS if i == FOCUS_AT else None)
    return _BOARD.copy(), info


def pump(cond, timeout=20.0):  # generous: the bound is never what a passing run depends on
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond(), "condition not reached in time"


def pictures():
    return [Picture(f"P{i}", ROOT / f"P{i}.JPG", None, (ROOT / f"P{i}.JPG",), "JPG", 1_000_000, 1.0)
            for i in range(N)]


class SpyPrefetcher(Prefetcher):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.updates: list[int] = []

    def update(self, i):
        self.updates.append(i)
        super().update(i)


@pytest.fixture
def made(monkeypatch):
    monkeypatch.setattr(MainWindow, "peaking_settle_ms", 0)  # no settle delay in tests: wait on state, not time
    wins = []

    def make(advanced=False, **kw):
        features.set_mode(features.ADVANCED if advanced else features.CLASSIC)
        cat = Catalog(pictures())
        pf = SpyPrefetcher(cat, loader=loader)
        win = MainWindow(cat, pf, ROOT, **kw)
        win.resize(1000, 700)
        win.show()
        win.activateWindow()
        QTest.qWaitForWindowActive(win, 3000)
        wins.append(win)
        return win

    yield make
    for win in wins:
        win.close()


def ready(w, upto=N - 1):
    pump(lambda: w.viewer.has_image and all(w.prefetcher.cached(i) is not None for i in range(upto + 1)))
    return w


def key(w, k):
    QTest.keyClick(w, k)


def prefs_bytes():
    p = Path(os.environ["SPUZA_SETTINGS_FILE"])
    return p.read_bytes() if p.exists() else None


def sessions_dir():
    return Path(os.environ["SPUZA_SESSIONS_DIR"])


# ---- classic: every key falls through, nothing exists, nothing is written ------------------------

def test_classic_keys_do_nothing_and_write_nothing(made):
    w = ready(made(), 2)
    before = prefs_bytes()
    key(w, Qt.Key_Space)  # a mark, to be sure U does not clear it
    for k in (Qt.Key_N, Qt.Key_P, Qt.Key_O, Qt.Key_U):
        key(w, k)
    assert w.stack.count() == 1 and w.stack.currentIndex() == 0 and not w._compare_shown()
    assert w.compare is None and w.peaking is None and w.marker is None and w.resume is None
    assert w.catalog.is_marked(0) and w.catalog.index == 0
    assert prefs_bytes() == before  # not one byte of settings written
    assert not sessions_dir().exists()  # no session file, no folder
    assert not w.viewer.image_item().childItems()  # no overlay / ring item on the picture
    assert not any(a.isVisible() for a in w._next_actions.values()) and not w.toolbar2.isVisible()
    assert not any(b.isVisible() for b in w.next_buttons.values())
    assert w.viewer.zoom_target() is None


def test_classic_buttons_exist_but_do_nothing(made):
    w = ready(made(), 1)
    for b in w.next_buttons.values():
        b.click()  # hidden, but a stray click must not act either
    assert w.stack.currentIndex() == 0 and w.peaking is None and w.marker is None
    assert not any(b.isChecked() for b in w.next_buttons.values())


def test_classic_z_ignores_the_focus_point(made):
    w = ready(made(), FOCUS_AT)
    w.catalog.go(FOCUS_AT)
    w._moved()
    pump(lambda: w.viewer.index == FOCUS_AT and w.viewer.has_image)
    assert w.viewer.info.focus is FOCUS  # the (fake) loader supplied it; classic still ignores it
    key(w, Qt.Key_Z)
    c = w.viewer.mapToScene(w.viewer.viewport().rect().center())
    assert abs(c.x() - W / 2) < 6 and abs(c.y() - H / 2) < 6


# ---- compare --------------------------------------------------------------------------------------

def test_n_opens_compare_on_this_and_the_next_picture(made):
    w = ready(made(True), 2)
    key(w, Qt.Key_N)
    assert w._compare_shown() and w.compare.indices() == [0, 1] and w.compare.focused_index() == 0
    assert w.filmstrip.highlight == {0, 1}
    assert w.next_buttons["compare"].isChecked() and not w.buttons["review"].isEnabled()
    key(w, Qt.Key_Right)  # compare owns the arrows: the viewer's picture does not move
    assert w.compare.focused_index() == 1 and w.catalog.index == 0
    key(w, Qt.Key_Space)  # marks the focused cell
    assert w.catalog.is_marked(1) and not w.catalog.is_marked(0)
    assert w.buttons["mark"].isChecked()  # lit for the focused cell
    key(w, Qt.Key_N)  # a run: grows to three
    assert w.compare.indices() == [0, 1, 2] and w.filmstrip.highlight == {0, 1, 2}
    QTest.qWait(30)
    key(w, Qt.Key_Escape)  # closes onto the focused picture
    assert not w._compare_shown() and w.stack.currentIndex() == 0
    assert w.catalog.index == 1 and w.viewer.index == 1
    assert w.filmstrip.highlight == frozenset() and w.buttons["review"].isEnabled()
    assert not w.next_buttons["compare"].isChecked()


def test_n_on_the_last_picture_compares_the_previous_one(made):
    w = ready(made(True))
    w.catalog.go(N - 1)
    w._moved()
    key(w, Qt.Key_N)
    assert w.compare.indices() == [N - 2, N - 1] and w.compare.focused_index() == N - 1


def test_compare_keys_and_buttons_route_to_compare(made):
    w = ready(made(True), 2)
    w.next_buttons["compare"].click()
    assert w._compare_shown()
    w.buttons["next"].click()  # the toolbar's controls drive compare while it is up
    assert w.compare.focused_index() == 1
    w.buttons["zoom"].click()
    assert w.compare.is_zoomed() and w.buttons["zoom"].isChecked() and not w.viewer.zoomed
    w.buttons["zoom"].click()
    assert not w.compare.is_zoomed()
    w.buttons["mark"].click()
    assert w.catalog.is_marked(1)
    w.buttons["back"].click()
    assert w.compare.focused_index() == 0
    w.next_buttons["compare"].click()  # N again: grows the run
    assert len(w.compare.indices()) == 3
    key(w, Qt.Key_R)  # R, U and friends are not compare's: nothing happens
    key(w, Qt.Key_U)
    assert w._compare_shown() and w.stack.currentIndex() == 2


def test_compare_marks_refresh_counts_and_move_on_mark(made):
    w = ready(made(True), 2)
    w.set_advance_on_mark(True, announce=False)
    key(w, Qt.Key_N)
    key(w, Qt.Key_Space)  # move on mark: focus goes to the next cell, never out of the set
    assert w.compare.focused_index() == 1 and w.catalog.is_marked(0)
    assert w.lbl_marked.text().startswith("1 marked") and w.sidebar.counts()["Marked"] == 1


def test_compare_from_review_uses_the_grid_selection(made):
    w = ready(made(True))
    for i in (1, 3, 5):
        w.catalog.set_mark(i, True)
    w.refresh_marks()
    w.buttons["review"].click()
    assert w.stack.currentIndex() == 1
    key(w, Qt.Key_N)  # one picture selected: a hint, nothing opens
    assert w.compare is None and "Select two" in w.last_message
    w.review.view.selectAll()
    key(w, Qt.Key_N)
    assert w._compare_shown() and w.compare.indices() == [1, 3, 5]
    key(w, Qt.Key_Space)  # unmark the focused one (1)
    assert not w.catalog.is_marked(1)
    key(w, Qt.Key_N)  # not a run: N closes
    assert not w._compare_shown() and w.stack.currentIndex() == 1  # back on the grid, refreshed
    assert w.review.marked_indices() == [3, 5] and w.review.model.rowCount() == 2
    assert w.catalog.index == 0 and not w.buttons["fullscreen"].isEnabled()
    w.buttons["review"].click()
    assert w.stack.currentIndex() == 0


def test_n_is_not_a_grid_key():
    from spuza.ui.window import GRID_KEYS

    assert Qt.Key_N not in GRID_KEYS


def test_compare_when_review_is_built_after_it(made):
    w = ready(made(True))
    key(w, Qt.Key_N)
    key(w, Qt.Key_Escape)
    w.catalog.set_mark(0, True)
    w.catalog.set_mark(1, True)
    w.buttons["review"].click()  # built after compare: still stack page 1
    assert w.stack.currentIndex() == 1 and w.stack.currentWidget() is w.review
    key(w, Qt.Key_Escape)
    key(w, Qt.Key_N)
    assert w._compare_shown()


def test_compare_in_full_bleed_shows_its_status(made):
    w = ready(made(True), 2)
    w.set_bleed(True)
    key(w, Qt.Key_N)
    assert w._compare_strip.isVisibleTo(w._compare_page) and w._compare_strip.text() == "compare 2 · 0 marked"
    assert not w.toolbar.isVisible() and not w.filmstrip.isVisible()
    key(w, Qt.Key_Space)
    assert w._compare_strip.text() == "compare 2 · 1 marked"
    key(w, Qt.Key_Escape)
    w.set_bleed(False)
    assert not w.is_bleed


def test_compare_peaking_overlay_per_cell(made):
    w = ready(made(True), 2)
    key(w, Qt.Key_P)
    assert w.peaking.is_on()
    key(w, Qt.Key_N)
    pump(lambda: all(w.compare.cell(i).overlay.isVisible() for i in (0, 1)))
    key(w, Qt.Key_P)  # off: the cells lose them
    assert not any(w.compare.cell(i).overlay.isVisible() for i in (0, 1))
    key(w, Qt.Key_Escape)


def test_nothing_reaches_a_closed_window_after_compare(made):
    w = ready(made(True), 2)
    key(w, Qt.Key_P)
    key(w, Qt.Key_N)
    w.close()  # shutdown of compare, the mask pool and the overlay, all bounded


# ---- peaking --------------------------------------------------------------------------------------

def test_p_toggles_peaking_and_saves_it(made):
    w = ready(made(True), 1)
    assert w.peaking is None  # not built (numpy not imported) until P or a saved-on state asks
    key(w, Qt.Key_P)
    assert w.peaking.is_on() and w.next_buttons["peaking"].isChecked()
    assert settings.read_peaking_on(settings.open_settings()) is True
    pump(lambda: w.peaking.mask_for(0) is not None)
    assert w.peaking._item.isVisible()
    key(w, Qt.Key_Right)
    pump(lambda: w.viewer.index == 1 and w.peaking.mask_for(1) is not None)  # the next picture gets its own
    key(w, Qt.Key_P)
    assert not w.peaking.is_on() and not w.peaking._item.isVisible()
    assert settings.read_peaking_on(settings.open_settings()) is False


def test_saved_peaking_comes_back_at_start_with_its_threshold(made):
    s = settings.open_settings()
    settings.write_peaking_on(s, True)
    settings.write_peaking_threshold(s, 90)
    s.sync()
    w = ready(made(True), 0)
    pump(lambda: w.peaking is not None and w.peaking.is_on())  # built once the prefetch went idle
    assert w.peaking._threshold == 90
    pump(lambda: w.peaking.mask_for(0) is not None)


# ---- the AF point ---------------------------------------------------------------------------------

def test_o_shows_the_ring_and_z_lands_on_the_focus_point(made):
    w = ready(made(True), FOCUS_AT)
    w.catalog.go(FOCUS_AT)
    w._moved()
    pump(lambda: w.viewer.index == FOCUS_AT and w.viewer.has_image)
    assert w.marker is None  # built on first O (or a saved-on state)
    key(w, Qt.Key_O)
    assert w.marker.is_on() and w.marker.visible() and w.next_buttons["afpoint"].isChecked()
    assert settings.read_focus_marker(settings.open_settings()) is True
    key(w, Qt.Key_Z)  # lands on the AF point (1000, 300), not the centre
    c = w.viewer.mapToScene(w.viewer.viewport().rect().center())
    assert abs(c.x() - 1000) < 6 and abs(c.y() - 300) < 6
    key(w, Qt.Key_Z)
    key(w, Qt.Key_Left)
    pump(lambda: w.viewer.index == FOCUS_AT - 1)
    assert not w.marker.visible()  # no focus data on this picture: no ring
    key(w, Qt.Key_Z)  # and Z centres as before
    c = w.viewer.mapToScene(w.viewer.viewport().rect().center())
    assert abs(c.x() - W / 2) < 6 and abs(c.y() - H / 2) < 6
    key(w, Qt.Key_O)
    assert not w.marker.is_on() and settings.read_focus_marker(settings.open_settings()) is False


def test_z_lands_on_the_focus_point_even_with_the_ring_off(made):
    w = ready(made(True), FOCUS_AT)
    w.catalog.go(FOCUS_AT)
    w._moved()
    pump(lambda: w.viewer.index == FOCUS_AT and w.viewer.has_image)
    key(w, Qt.Key_Z)
    c = w.viewer.mapToScene(w.viewer.viewport().rect().center())
    assert abs(c.x() - 1000) < 6 and abs(c.y() - 300) < 6


def test_review_100_percent_centres_on_the_focus_point(made):
    w = ready(made(True))
    for i in (1, FOCUS_AT):
        w.catalog.set_mark(i, True)
    w.refresh_marks()
    w.buttons["review"].click()
    w.review.view.setCurrentIndex(w.review.model.index(w.review._row_of(FOCUS_AT), 0))
    w.review.show_zoom()
    pump(lambda: w.review._zoom_item is not None and w.review._zoom_item.pixmap().width() == W)
    w.review.toggle_zoom()  # fit -> 100%
    assert not w.review._zoom_fit
    c = w.review.zoom_view.mapToScene(w.review.zoom_view.viewport().rect().center())
    assert abs(c.x() - 1000) < 6 and abs(c.y() - 300) < 6
    w.review.leave_zoom()
    w.review.view.setCurrentIndex(w.review.model.index(w.review._row_of(1), 0))  # no focus data: centre
    w.review.show_zoom()
    pump(lambda: w.review._zoom_item is not None and w.review._zoom_item.pixmap().width() == W)
    w.review.toggle_zoom()
    c = w.review.zoom_view.mapToScene(w.review.zoom_view.viewport().rect().center())
    assert abs(c.x() - W / 2) < 6 and abs(c.y() - H / 2) < 6


def test_review_zoom_survives_a_picture_without_info():
    from spuza.ui.review import ReviewGrid

    class NoInfo:
        def cached(self, i):
            return None

        def info(self, i):
            return None

    features.set_mode(features.ADVANCED)
    g = ReviewGrid(Catalog(pictures()), NoInfo())
    assert g._focus_frac(0) is None and g._focus_frac(None) is None


# ---- resume ---------------------------------------------------------------------------------------

def session_files():
    d = sessions_dir()
    return sorted(d.glob("*.json")) if d.exists() else []


def test_quit_and_reopen_restores_marks_and_place_before_the_first_paint(made):
    w1 = ready(made(True), 3)
    for i in (1, 3):
        w1.catalog.go(i)
        w1.toggle_mark()
    w1.catalog.go(4)
    w1._moved()
    w1.close()
    files = session_files()
    assert len(files) == 1
    doc = json.loads(files[0].read_text())
    assert [m["name"] for m in doc["marks"]] == ["P1.JPG", "P3.JPG"] and doc["index_name"] == "P4.JPG"

    w2 = made(True)
    assert w2.catalog.index == 4 and w2.catalog.is_marked(1) and w2.catalog.is_marked(3)
    assert w2.prefetcher.updates[0] == 4  # the first prefetch was already at the restored place
    pump(lambda: w2.viewer.has_image)
    assert w2.viewer.index == 4
    assert w2.last_message.startswith("Restored 2 marks and your place (picture 5)") and "U to start fresh" in w2.last_message
    assert w2.lbl_marked.text().startswith("2 marked")


def test_u_starts_fresh_and_a_second_u_undoes_it(made):
    w1 = ready(made(True), 2)
    w1.toggle_mark()
    w1.close()
    assert len(session_files()) == 1
    w2 = made(True)
    pump(lambda: w2.viewer.has_image)
    assert w2.catalog.is_marked(0)
    key(w2, Qt.Key_U)
    assert not w2.catalog.is_marked(0) and w2.lbl_marked.text().startswith("0 marked")
    assert "Started fresh: 1 mark cleared" in w2.last_message and session_files() == []
    key(w2, Qt.Key_U)
    assert w2.catalog.is_marked(0) and w2.last_message == "Marks back (1)"
    assert len(session_files()) == 1


def test_resume_pref_off_neither_reads_nor_writes(made):
    s = settings.open_settings()
    settings.write_resume_on(s, False)
    s.sync()
    w1 = ready(made(True), 2)
    w1.toggle_mark()
    w1.close()
    assert session_files() == []
    w2 = made(True)
    assert not w2.catalog.is_marked(0)


def test_a_different_card_does_not_restore(made):
    w1 = ready(made(True), 2)
    w1.toggle_mark()
    w1.close()
    features.set_mode(features.ADVANCED)
    pics = pictures()[:-1]  # one picture fewer: another card
    cat = Catalog(pics)
    w2 = MainWindow(cat, Prefetcher(cat, loader=loader), ROOT)
    assert cat.marked_count == 0 and cat.index == 0
    w2.close()


def test_old_sessions_are_pruned_once_in_advanced_only(made, tmp_path):
    d = sessions_dir()
    d.mkdir(parents=True)
    old = d / "oldkey.json"
    old.write_text(json.dumps({"version": 1, "saved_at": time.time() - 40 * 86400, "marks": [], "index": 0}))
    fresh = d / "freshkey.json"
    fresh.write_text(json.dumps({"version": 1, "saved_at": time.time() - 86400, "marks": [], "index": 0}))
    made(False)
    assert old.exists()  # classic never touches the folder
    w = made(True)
    w._prune_thread.join(5)
    assert not w._prune_thread.is_alive()
    assert not old.exists() and fresh.exists()


# ---- the live mode switch -------------------------------------------------------------------------

def test_switching_to_classic_hides_everything_and_back_restores_it(made):
    s = settings.open_settings()
    settings.write_peaking_on(s, True)
    settings.write_focus_marker(s, True)
    s.sync()
    w = ready(made(True), FOCUS_AT)
    w.catalog.go(FOCUS_AT)
    w._moved()
    pump(lambda: w.viewer.index == FOCUS_AT and w.viewer.has_image)
    pump(lambda: w.peaking is not None and w.peaking.is_on())
    assert all(w._next_actions[n].isVisible() for n in w.next_buttons) and w.marker.visible()
    key(w, Qt.Key_N)
    assert w._compare_shown()
    w.catalog.set_mark(0, True)

    w.set_advanced(False)
    assert not w._compare_shown() and w.stack.currentIndex() == 0
    assert not w.peaking.is_on() and not w.marker.is_on() and not w.marker.visible()
    assert not any(a.isVisible() for a in w._next_actions.values())
    assert w.filmstrip.highlight == frozenset()
    assert settings.read_peaking_on(settings.open_settings()) is True  # the saved choice is untouched
    for k in (Qt.Key_N, Qt.Key_P, Qt.Key_O, Qt.Key_U):
        key(w, k)
    assert w.stack.currentIndex() == 0 and not w.peaking.is_on() and w.catalog.is_marked(0)
    assert session_files()  # the flush on the way out saved the marks

    w.set_advanced(True)
    assert w.peaking.is_on() and w.marker.is_on()
    assert all(w._next_actions[n].isVisible() for n in w.next_buttons)
    assert w.next_buttons["peaking"].isChecked() and w.next_buttons["afpoint"].isChecked()
    pump(lambda: w.marker.visible())
    key(w, Qt.Key_N)
    assert w._compare_shown()


def test_switching_to_advanced_live_starts_resume_and_restores_a_pristine_card(made):
    w1 = ready(made(True), 2)
    w1.toggle_mark()
    w1.close()
    w2 = ready(made(False), 1)
    assert w2.resume is None and not w2.catalog.is_marked(0)
    w2.set_advanced(True)
    assert w2.resume is not None and w2.catalog.is_marked(0)
    pump(lambda: w2.last_message.startswith("Restored"))


def test_switching_to_advanced_live_does_not_restore_over_work_already_done(made):
    w1 = ready(made(True), 2)
    w1.toggle_mark()
    w1.close()
    w2 = ready(made(False), 1)
    w2.catalog.go(2)
    w2._moved()
    w2.set_advanced(True)
    assert not w2.catalog.is_marked(0) and w2.catalog.index == 2


# ---- the settings file stays byte-identical -------------------------------------------------------

def test_classic_run_leaves_a_seeded_settings_file_byte_identical(made):
    s = settings.open_settings()
    settings.write_window(s, 7, 3)
    s.sync()
    before = prefs_bytes()
    w = ready(made(False), 2)
    for k in (Qt.Key_N, Qt.Key_P, Qt.Key_O, Qt.Key_U, Qt.Key_Space, Qt.Key_Right):
        key(w, k)
    w.close()
    assert prefs_bytes() == before


def test_window_with_resume_false_neither_restores_nor_saves(made):
    w1 = ready(made(True), 2)
    w1.toggle_mark()
    w1.close()
    before = session_files()[0].read_bytes()
    features.set_mode(features.ADVANCED)
    cat = Catalog(pictures())
    w2 = MainWindow(cat, Prefetcher(cat, loader=loader), ROOT, resume=False)
    assert w2.resume is None and cat.marked_count == 0 and cat.index == 0
    cat.set_mark(3, True)
    key(w2, Qt.Key_U)
    w2.close()
    assert w2.resume is None and session_files()[0].read_bytes() == before


def test_bench_runs_without_resume(tmp_path, monkeypatch):
    """--bench starts at picture 1 and must not read or overwrite a real session."""
    from spuza import cli
    from PySide6.QtWidgets import QApplication

    for i in range(2):
        Image.new("RGB", (32, 24), (i * 90, 10, 10)).save(tmp_path / f"P{i}.JPG")
    seen = {}

    def fake_bench(app, window):
        seen["allowed"] = window._resume_allowed
        seen["resume"] = window.resume
        return 0

    monkeypatch.setattr(cli, "_app", lambda: QApplication.instance())
    monkeypatch.setattr(cli, "_wait", lambda *a, **k: True)
    monkeypatch.setattr(cli, "run_bench", fake_bench)
    assert cli.main(["--demo", str(tmp_path), "--bench", "--advanced"]) == 0
    assert seen == {"allowed": False, "resume": None}
    assert session_files() == []


def test_k_is_next_and_j_is_back_inside_compare(made):
    w = ready(made(True), 2)
    key(w, Qt.Key_N)
    key(w, Qt.Key_K)
    assert w.compare.focused_index() == 1
    key(w, Qt.Key_J)
    assert w.compare.focused_index() == 0


class BusyPrefetcher(SpyPrefetcher):
    """busy() is under the test's control, so the deferred build is driven by state, not by time."""

    is_busy = True

    def busy(self):
        return self.is_busy


def test_saved_peaking_is_built_only_when_the_prefetch_is_idle(made):
    s = settings.open_settings()
    settings.write_peaking_on(s, True)
    s.sync()
    features.set_mode(features.ADVANCED)
    cat = Catalog(pictures())
    pf = BusyPrefetcher(cat, loader=loader)
    w = MainWindow(cat, pf, ROOT)
    w.show()
    try:
        pump(lambda: all(pf.cached(i) is not None for i in range(N)))
        QTest.qWait(50)
        assert w.peaking is None and w._peaking_wait  # busy: numpy is not imported yet
        pf.is_busy = False
        pf.ready.emit(0, pf.cached(0), None)  # the next ready notices it went idle
        assert w.peaking is not None and w.peaking.is_on() and not w._peaking_wait
        pump(lambda: w.peaking.mask_for(0) is not None)
    finally:
        w.close()


def test_a_prefetcher_without_busy_counts_as_idle(made):
    s = settings.open_settings()
    settings.write_peaking_on(s, True)
    s.sync()
    features.set_mode(features.ADVANCED)
    cat = Catalog(pictures())
    pf = SpyPrefetcher(cat, loader=loader)
    pf.busy = None  # shadows the method: a fake with no usable busy()
    w = MainWindow(cat, pf, ROOT)
    w.show()
    try:
        pump(lambda: w.peaking is not None and w.peaking.is_on())
    finally:
        w.close()
