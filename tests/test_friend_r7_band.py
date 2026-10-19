"""Friend round 7, window side: a crop (X) or story fit (S) made with Enter is its own picture on the filmstrip, right
after its original. The original stays clean and can be cropped again; the derived picture has a thumbnail cut from
the original's, a tag, a mark, and can be viewed, re-cropped, marked, copied and removed. Synthetic pictures only
(offscreen); the 64 x 48 loader preview is told to be a 6000 x 4000 picture through the prefetcher's info."""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza import derived as dv
from spuza import features
from spuza.catalog import Catalog, Picture
from spuza.crop import crop_box
from spuza.ratio import FREE, Ratio
from spuza.story import Story
from spuza.ui import thumbs
from spuza.ui.filmstrip import Filmstrip
from test_friend_r3_window import CTRL, CTRL_SHIFT, make_window, pump

CROP = (0.1, 0.2, 0.6, 0.7)
CROP2 = (0.0, 0.0, 0.5, 0.5)


@pytest.fixture
def w():
    win = make_window(marked=())
    orig = win.prefetcher.info

    def info(i):
        got = orig(i)
        return dataclasses.replace(got, picture_size=(6000, 4000)) if got is not None else None

    win.prefetcher.info = info
    win.refresh_marks()
    yield win
    win.close()


def key(win, k, mod=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(win, k, mod)


def shown(win):
    pump(lambda: win.prefetcher.cached(win.catalog.index) is not None and win.viewer.has_image)


def crop_with(win, rect=CROP, ratio_key=None):
    shown(win)
    key(win, Qt.Key_X)
    if ratio_key is not None:
        key(win, ratio_key)
    win._crop_overlay.set_rect_frac(rect)
    key(win, Qt.Key_Return)


def story_with(win, story=Story(2.0, 0.0, 0.0)):
    shown(win)
    key(win, Qt.Key_S)
    win._story_editor._story = story
    key(win, Qt.Key_Return)


def rows(win):
    return list(win.band.rows(len(win.catalog)))


# -- Enter makes a picture ---------------------------------------------------------------------------------------
def test_enter_in_an_x_edit_adds_a_derived_picture_after_the_original(w):
    crop_with(w)
    (d,) = w.band.items
    assert (d.source, d.kind, d.crop, d.ratio, d.marked) == (0, dv.CROP, CROP, None, True)
    assert rows(w)[:3] == [(0, None), (0, d.id), (1, None)] and len(rows(w)) == 7
    assert w.filmstrip.model().rowCount() == 7
    assert w.catalog.index == 0 and w._cur_id == d.id and w.filmstrip.current_id == d.id  # selection is on it
    assert not hasattr(w, "crops") and not hasattr(w, "stories") and not hasattr(w, "crop_ratios")
    assert not w._crop_overlay.editing and "Cropped P0" in w.last_message


def test_the_original_stays_clean_and_a_second_x_makes_a_second_derived(w):
    crop_with(w)
    w.go_to(0)  # back on the original
    assert w._cur_id is None
    key(w, Qt.Key_X)
    assert w._crop_overlay.rect_frac() is None and w._crop_ratio == FREE  # nothing preselected on an original
    w._crop_overlay.set_rect_frac(CROP2)
    key(w, Qt.Key_Return)
    first, second = w.band.items
    assert (first.crop, second.crop) == (CROP, CROP2) and first.id != second.id
    assert rows(w)[:4] == [(0, None), (0, first.id), (0, second.id), (1, None)]
    assert w._cur_id == second.id


def test_enter_with_no_rectangle_creates_nothing_and_escape_cancels(w):
    shown(w)
    key(w, Qt.Key_X)
    key(w, Qt.Key_Return)
    assert not w.band and w._edit is None and w._cur_id is None
    key(w, Qt.Key_X)
    w._crop_overlay.set_rect_frac(CROP)
    key(w, Qt.Key_Escape)
    assert not w.band and not w._crop_overlay.editing and w.last_message == "Cancelled: nothing changed"


def test_the_ratio_is_kept_on_the_derived_picture(w):
    crop_with(w, ratio_key=Qt.Key_4)
    (d,) = w.band.items
    assert d.ratio == Ratio(4)
    w.go_to(0)
    crop_with(w, ratio_key=Qt.Key_1)
    assert w.band.items[1].ratio is None  # Free is None


def test_s_adds_a_derived_story(w):
    story_with(w, Story(1.5, 0.0, 0.0))
    (d,) = w.band.items
    assert (d.source, d.kind, d.story, d.marked) == (0, dv.STORY, Story(1.5, 0.0, 0.0), True)
    assert rows(w)[:3] == [(0, None), (0, d.id), (1, None)] and w._cur_id == d.id
    assert "Story fit on P0" in w.last_message


# -- X on a derived crop is a re-crop; other combinations start a new one -----------------------------------------
def test_x_on_a_derived_crop_recrops_it_in_place(w):
    crop_with(w, ratio_key=Qt.Key_4)
    (d,) = w.band.items
    w.band.set_marked(d.id, False)
    shown(w)
    key(w, Qt.Key_X)
    # the editor works on the SOURCE's pixels, with the rectangle and ratio preselected
    assert (w.viewer.image_item().pixmap().width(), w.viewer.image_item().pixmap().height()) == (64, 48)
    assert w._crop_overlay.rect_frac() == CROP and w._crop_ratio == Ratio(4)
    w._crop_overlay.set_rect_frac(CROP2)
    key(w, Qt.Key_Return)
    (n,) = w.band.items
    assert (n.id, n.source, n.crop, n.marked) == (d.id, 0, CROP2, False)  # same id, place and mark
    assert n.ratio == Ratio(4) and w._cur_id == d.id and len(rows(w)) == 7
    assert "Cropped P0" in w.last_message


def test_escape_in_a_recrop_leaves_the_derived_picture_as_it_was_and_shows_it_again(w):
    crop_with(w)
    (d,) = w.band.items
    shown(w)
    key(w, Qt.Key_X)
    w._crop_overlay.set_rect_frac(CROP2)
    key(w, Qt.Key_Escape)
    assert w.band.items == (d,) and w._cur_id == d.id
    pm = w.viewer.image_item().pixmap()
    x0, y0, x1, y1 = crop_box((64, 48), CROP)
    assert (pm.width(), pm.height()) == (x1 - x0, y1 - y0)  # the crop is shown again


def test_s_on_a_derived_crop_and_x_on_a_derived_story_start_new_ones(w):
    crop_with(w)
    story_with(w)
    assert [d.kind for d in w.band.items] == [dv.CROP, dv.STORY]
    key(w, Qt.Key_X)  # on the derived story: a NEW crop from the source
    assert w._crop_overlay.rect_frac() is None
    w._crop_overlay.set_rect_frac(CROP2)
    key(w, Qt.Key_Return)
    assert [d.kind for d in w.band.items] == [dv.CROP, dv.STORY, dv.CROP]
    key(w, Qt.Key_S)  # on a derived crop: a NEW story
    key(w, Qt.Key_Return)
    assert [d.kind for d in w.band.items] == [dv.CROP, dv.STORY, dv.CROP, dv.STORY]


def test_s_on_a_derived_story_refits_it_in_place(w):
    story_with(w, Story(1.2, 0.0, 0.0))
    (d,) = w.band.items
    key(w, Qt.Key_S)
    assert w._story_editor.story().zoom == pytest.approx(1.2)
    w._story_editor._story = Story(1.8, 0.0, 0.0)
    key(w, Qt.Key_Return)
    (n,) = w.band.items
    assert n.id == d.id and n.story == Story(1.8, 0.0, 0.0) and w._cur_id == d.id


def test_moving_to_another_row_cancels_an_open_edit(w):
    crop_with(w)
    key(w, Qt.Key_X)
    key(w, Qt.Key_J)
    assert w._edit is None and w.catalog.index == 1 and len(w.band) == 1


# -- Backspace --------------------------------------------------------------------------------------------------
def test_backspace_removes_a_derived_picture_and_keeps_the_original(w):
    crop_with(w)
    story_with(w)
    first, second = w.band.items
    key(w, Qt.Key_Backspace)
    assert w.band.items == (first,) and w._cur_id == first.id  # the previous band row
    assert "Removed" in w.last_message and "P0" in w.last_message
    key(w, Qt.Key_Backspace)
    assert not w.band and w._cur_id is None and w.catalog.index == 0 and len(w.catalog) == 6
    assert len(rows(w)) == 6


def test_backspace_on_an_original_does_nothing_and_says_so(w):
    key(w, Qt.Key_Backspace)
    assert w.last_message == "Nothing to remove here" and not w.band


def test_backspace_inside_a_crop_edit_clears_the_rectangle_and_enter_then_adds_nothing(w):
    shown(w)
    key(w, Qt.Key_X)
    w._crop_overlay.set_rect_frac(CROP)
    key(w, Qt.Key_Backspace)
    assert w._crop_overlay.editing and w._crop_overlay.rect_frac() is None
    key(w, Qt.Key_Return)
    assert not w.band


def test_x_again_no_longer_clears(w):
    crop_with(w)
    key(w, Qt.Key_X)
    key(w, Qt.Key_X)
    assert w._edit == "crop" and len(w.band) == 1


# -- stepping, marks, counts ----------------------------------------------------------------------------------------
def test_j_and_k_step_through_the_derived_rows(w):
    crop_with(w)
    d = w.band.items[0]
    w.go_to(0)
    key(w, Qt.Key_J)
    assert (w.catalog.index, w._cur_id) == (0, d.id) and w.filmstrip.current == 0
    key(w, Qt.Key_J)
    assert (w.catalog.index, w._cur_id) == (1, None)
    key(w, Qt.Key_K)
    assert (w.catalog.index, w._cur_id) == (0, d.id)
    key(w, Qt.Key_Left)
    assert (w.catalog.index, w._cur_id) == (0, None)
    key(w, Qt.Key_K)  # the first row: stays
    assert (w.catalog.index, w._cur_id) == (0, None)


def test_a_click_picks_a_row(w):
    crop_with(w)
    d = w.band.items[0]
    w.go_to(2)
    w.filmstrip.picked_derived.emit(0, d.id)
    assert (w.catalog.index, w._cur_id) == (0, d.id)
    w.filmstrip.picked.emit(0)
    assert (w.catalog.index, w._cur_id) == (0, None)
    w.filmstrip.picked_derived.emit(3, 99)  # no such derived picture: ignored
    assert (w.catalog.index, w._cur_id) == (0, None)


def test_space_and_m_mark_the_derived_picture_not_the_original(w):
    crop_with(w)
    d = w.band.items[0]
    key(w, Qt.Key_Space)
    assert w.band.get(d.id).marked is False and not w.catalog.is_marked(0)
    key(w, Qt.Key_M)
    assert w.band.get(d.id).marked is True and w.catalog.marked_count == 0
    assert w.buttons["mark"].isChecked()


def test_sidebar_counts_include_the_derived_pictures(w):
    assert w.sidebar.counts() == {"All": 6, "Marked": 0}
    crop_with(w)
    assert w.sidebar.counts() == {"All": 7, "Marked": 1}  # a new derived picture is made marked
    w.catalog.set_mark(2, True)
    w.refresh_marks()
    assert w.sidebar.counts() == {"All": 7, "Marked": 2}
    key(w, Qt.Key_Space)
    assert w.sidebar.counts() == {"All": 7, "Marked": 1}


def test_mark_all_includes_derived_and_undo_restores_them(w):
    crop_with(w)
    story_with(w)
    a, b = w.band.items
    w.band.set_marked(a.id, False)  # a unmarked, b marked
    w.catalog.set_mark(3, True)
    key(w, Qt.Key_A, CTRL)
    assert all(d.marked for d in w.band) and w.catalog.marked_count == 6
    assert w.sidebar.counts() == {"All": 8, "Marked": 8}
    assert w.last_message.startswith("Marked 6 more, 8 in all")
    key(w, Qt.Key_Z, CTRL)
    assert [d.marked for d in w.band] == [False, True] and w.catalog.marked_count == 1
    assert w.sidebar.counts() == {"All": 8, "Marked": 2}
    key(w, Qt.Key_A, CTRL)
    key(w, Qt.Key_A, CTRL_SHIFT)
    assert not any(d.marked for d in w.band) and w.catalog.marked_count == 0
    assert w.last_message.startswith("Unmarked 8")
    key(w, Qt.Key_Z, CTRL)
    assert all(d.marked for d in w.band) and w.catalog.marked_count == 6


def test_undo_leaves_a_derived_picture_made_since_alone(w):
    crop_with(w)
    w.band.set_marked(w.band.items[0].id, False)
    key(w, Qt.Key_A, CTRL)
    w.go_to(2)
    crop_with(w, CROP2)  # made after Mark all, marked
    key(w, Qt.Key_Z, CTRL)
    assert [d.marked for d in w.band] == [False, True]  # the first goes back; the later one is not touched


# -- copy -------------------------------------------------------------------------------------------------------
def test_copy_factory_gets_only_the_marked_derived_pairs_even_with_the_original_unmarked(w):
    crop_with(w)
    story_with(w)
    w.go_to(1)
    w.catalog.set_mark(1, True)
    a, b = w.band.items
    w.band.set_marked(b.id, False)
    got = {}

    def factory(pictures, default_dest=None, parent=None, derived=()):
        got.update(pictures=list(pictures), derived=list(derived))

        class Sheet:
            def show(self):
                got["shown"] = True

        return Sheet()

    w.copy_sheet_factory = factory
    w.open_copy()
    assert got["pictures"] == [w.catalog.pictures[1]]
    assert got["derived"] == [(w.catalog.pictures[0], a)]  # the original (0) is not marked, its crop is
    assert got["shown"]


def test_copy_opens_when_only_derived_pictures_are_marked(w):
    crop_with(w)
    seen = []
    w.copy_sheet_factory = lambda pictures, default_dest=None, parent=None, derived=(): (
        seen.append((list(pictures), list(derived))) or type("S", (), {"show": lambda s: None})())
    w.open_copy()
    assert seen == [([], [(w.catalog.pictures[0], w.band.items[0])])]
    assert not w.last_message.startswith("Nothing is marked")
    w.band.set_marked(w.band.items[0].id, False)
    seen.clear()
    w.open_copy()
    assert seen == [] and w.last_message.startswith("Nothing is marked")


def test_copy_without_derived_pictures_calls_the_factory_as_before(w):
    w.catalog.set_mark(0, True)
    seen = []

    def old_factory(pictures, default_dest=None, parent=None):  # a factory that knows nothing of derived pictures
        seen.append(list(pictures))
        return type("S", (), {"show": lambda s: None})()

    w.copy_sheet_factory = old_factory
    w.open_copy()
    assert seen == [[w.catalog.pictures[0]]]


def test_the_default_sheet_gets_the_sources_turns_when_only_a_derived_picture_is_marked(w):
    crop_with(w)
    w.rotations[0] = 1
    pics = []
    sheet = w._default_copy_sheet_factory(pics, derived=[(w.catalog.pictures[0], w.band.items[0])])
    assert sheet.rotations == {w.catalog.pictures[0].primary: 1}


# -- the strip ----------------------------------------------------------------------------------------------------
def test_filmstrip_rows_and_tags(w):
    crop_with(w, ratio_key=Qt.Key_4)
    story_with(w)
    s = w.filmstrip
    a, b = w.band.items
    assert s.model().rowCount() == 8
    assert s.row_tags(0, None) == []  # an original carries no edit tag
    assert s.row_tags(0, a.id) == [("4:5", "crop")] and s.row_tags(0, b.id) == [("9:16", "story")]
    w.band.add(0, dv.CROP, crop=CROP)  # no ratio: Free
    assert s.row_tags(0, 3) == [("crop", "crop")]
    w.rotations[0] = 1
    assert s.row_tags(0, a.id) == [("5:4", "crop")]  # named as seen on screen
    cell = s.visualRect(s.model().index(1))
    assert s.viewport().grab(cell).toImage().width() > 0  # paints without raising


def test_the_strip_follows_an_added_and_a_removed_row(w):
    n0 = w.filmstrip.model().rowCount()
    crop_with(w)
    assert w.filmstrip.model().rowCount() == n0 + 1
    key(w, Qt.Key_Backspace)
    assert w.filmstrip.model().rowCount() == n0


def test_right_click_on_a_derived_cell_reveals_the_original(w, monkeypatch):
    from spuza import reveal as rv

    got = []
    monkeypatch.setattr(rv, "reveal", lambda p: got.append(p))
    crop_with(w)
    s = w.filmstrip
    menu = s.context_menu_for(s.visualRect(s.model().index(1)).center())
    (action,) = menu.actions()
    action.trigger()
    assert got == [w.catalog.pictures[0].primary]


def test_the_viewer_shows_the_crop_of_the_sources_pixels_and_zoom_works_on_the_crop(w):
    crop_with(w)
    pm = w.viewer.image_item().pixmap()
    x0, y0, x1, y1 = crop_box((64, 48), CROP)
    assert (pm.width(), pm.height()) == (x1 - x0, y1 - y0) and w.viewer.index == 0
    # Z = 100% of the crop region of the native pixels: 6000 x 4000 source -> 3000 x 2000
    assert w.viewer._native_size() == (3000, 2000)
    assert w.viewer._preview_ratio() == pytest.approx(3000 / pm.width())
    key(w, Qt.Key_Z)
    assert w.viewer.zoomed
    key(w, Qt.Key_Z)
    w.go_to(0)  # the original: the whole picture again
    assert (w.viewer.image_item().pixmap().width(), w.viewer.image_item().pixmap().height()) == (64, 48)


def test_the_viewer_shows_the_9_16_story_frame(w):
    story_with(w, Story(1.0, 0.0, 0.0))
    pm = w.viewer.image_item().pixmap()
    assert pm.width() * 16 == pm.height() * 9
    w.rotations[0] = 1  # a turn re-frames a story (it is placed in the turned picture)
    w._render_current()
    pm = w.viewer.image_item().pixmap()
    assert pm.width() * 16 == pm.height() * 9 and w.viewer._quarters() == 0


def test_turning_a_derived_picture_turns_its_source(w):
    crop_with(w)
    key(w, Qt.Key_BracketRight)
    assert w.rotations == {0: 1} and w.viewer.image_item().rotation() == 90
    w.band.add(0, dv.STORY, story=Story(1.0, 0, 0))
    w._cur_id = 2
    w._render_current()
    key(w, Qt.Key_BracketRight)
    assert w.rotations == {0: 2} and w.viewer.image_item().rotation() == 0  # a story picture is already framed


def test_inspector_lines_for_a_derived_crop_and_story(w):
    crop_with(w, ratio_key=Qt.Key_4)
    assert "Crop of P0.JPG" in w.inspector.lines
    assert "4:5" in w.inspector.lines and "3000 x 2000 px" in w.inspector.lines  # the drawn rectangle of 6000 x 4000
    assert w.inspector.lines[-1] == "Marked"
    story_with(w)
    assert "Story fit of P0.JPG" in w.inspector.lines
    assert "9:16" in w.inspector.lines and "1080 x 1920 px" in w.inspector.lines
    w.go_to(1)
    assert not any(t.startswith(("Crop of", "Story fit of")) for t in w.inspector.lines)
    w.go_to(0)
    crop_with(w, ratio_key=Qt.Key_1)
    assert "Free" in w.inspector.lines


def test_review_and_compare_stay_originals_only(w):
    crop_with(w)
    w.catalog.set_mark(2, True)
    w.open_review()
    assert w.review.model.rowCount() == 1  # the marked original only
    w.close_review()


# -- the session ---------------------------------------------------------------------------------------------------
def test_the_resume_controller_gets_the_shared_band(w, tmp_path, monkeypatch):
    features.set_mode(features.ADVANCED)
    monkeypatch.setenv("SPUZA_SESSIONS_DIR", str(tmp_path / "sessions"))
    seen = {}

    class Fake:
        def __init__(self, *a, **kw):
            seen.update(kw)

        def set_enabled(self, on):
            pass

        def restore(self):
            return None

        def flush(self):
            pass

    monkeypatch.setattr("spuza.ui.resume.ResumeController", Fake)
    w._ensure_resume()
    assert seen["band"] is w.band and seen["rotations"] is w.rotations
    assert "crops" not in seen and "stories" not in seen and "crop_ratios" not in seen


# -- performance: the strip reads memory only ------------------------------------------------------------------------
class _Holds:
    """A prefetcher stand-in whose cached() is a dict lookup, like the real one."""

    def __init__(self, n, img=None):
        self.n, self.img = n, img

    def cached(self, i):
        return self.img


def _pics(n):
    return [Picture(f"P{i}", Path(f"/nope/P{i}.JPG"), None, (Path(f"/nope/P{i}.JPG"),), "JPG", 1, float(i))
            for i in range(n)]


def _img(w_=64, h_=48, left="#c03030", right="#3030c0"):
    img = QImage(w_, h_, QImage.Format.Format_RGB888)
    img.fill(QColor(left))
    for x in range(w_ // 2, w_):
        for y in range(h_):
            img.setPixelColor(x, y, QColor(right))
    return img


def test_painting_a_big_strip_does_no_file_io_on_the_ui_thread_and_does_not_rebuild_the_rows(monkeypatch):
    main = threading.current_thread()
    calls = []

    def spy(*a, **kw):
        calls.append(threading.current_thread() is main)
        raise OSError("no file I/O on the UI thread")

    from spuza import preview

    monkeypatch.setattr(preview, "load", spy)
    monkeypatch.setattr(thumbs, "_decode_thumb", lambda *a, **kw: spy() if threading.current_thread() is main else None)
    cat = Catalog(_pics(2000))
    band = dv.Band()
    for k in range(500):
        band.add(k * 4, dv.CROP if k % 2 else dv.STORY, crop=CROP if k % 2 else None,
                 story=None if k % 2 else Story(1.5, 0.0, 0.0))
    s = Filmstrip(cat, _Holds(2000, _img()), band=band)
    s.resize(1200, 120)
    s.show()
    QApplication.processEvents()
    assert s.model().rowCount() == 2500
    first = band.rows(2000)
    for pos in (0, 5000, 100_000, 250_000):
        s.horizontalScrollBar().setValue(min(pos, s.horizontalScrollBar().maximum()))
        s.request_visible()
        s.viewport().grab()
    assert band.rows(2000) is first  # the Band's cache served every paint: rows() was not rebuilt
    builds = []
    orig = dv.Band._by_source
    monkeypatch.setattr(dv.Band, "_by_source", lambda self: builds.append(1) or orig(self))
    s.viewport().grab()
    assert builds == []
    assert not [c for c in calls if c]  # nothing ran on the main thread
    s.shutdown()


def test_a_derived_cell_without_its_source_thumbnail_is_a_placeholder_and_asks_for_the_source(monkeypatch):
    cat = Catalog(_pics(10))
    band = dv.Band()
    d = band.add(3, dv.CROP, crop=CROP)
    s = Filmstrip(cat, _Holds(10, None), band=band)
    s.resize(1200, 120)
    asked = []
    monkeypatch.setattr(s._loader, "request", lambda idx, w_, h_: asked.append(list(idx)))
    assert s.derived_thumb(d) is None
    s.request_visible()
    assert asked and 3 in asked[-1] and len(asked[-1]) == len(set(asked[-1]))  # the source, once
    s.shutdown()


def test_a_derived_thumbnail_is_cut_from_the_sources_thumbnail_and_dropped_when_the_item_changes():
    cat = Catalog(_pics(3))
    band = dv.Band()
    d = band.add(0, dv.CROP, crop=(0.5, 0.0, 1.0, 1.0))  # the right half: blue
    s = Filmstrip(cat, _Holds(3, _img()), band=band)
    s.resize(600, 120)
    pm = s.derived_thumb(d)
    img = pm.toImage()
    c = img.pixelColor(img.width() // 2, img.height() // 2)
    assert c.blue() > 150 and c.red() < 100
    assert s.derived_thumb(d) is pm  # memoised
    d2 = band.replace(d.id, crop=(0.0, 0.0, 0.5, 1.0))  # the left half: red
    img2 = s.derived_thumb(d2).toImage()
    c2 = img2.pixelColor(img2.width() // 2, img2.height() // 2)
    assert c2.red() > 150 and c2.blue() < 100
    assert len(s._dmemo) == 1  # one entry per derived picture: the old cut is gone
    st = band.add(0, dv.STORY, story=Story(1.0, 0.0, 0.0))
    spm = s.derived_thumb(st)
    assert spm.deviceIndependentSize().height() > spm.deviceIndependentSize().width()  # portrait 9:16
    s.rotation_of = lambda i: 1
    assert s.derived_thumb(d2) is not None
    s.shutdown()


def test_the_pure_cut_helpers():
    img = _img(40, 20)
    cut = thumbs.cut_crop(img, (0.5, 0.0, 1.0, 1.0))
    assert (cut.width(), cut.height()) == (20, 20) and cut.pixelColor(10, 10).blue() > 150
    fr = thumbs.story_frame(img, 0, Story(1.0, 0.0, 0.0), 90, 160)
    assert (fr.width(), fr.height()) == (90, 160)
    assert fr.pixelColor(45, 5).red() == 0 and fr.pixelColor(45, 5).blue() == 0  # black above the fitted picture
    assert fr.pixelColor(10, 80).red() > 150 and fr.pixelColor(80, 80).blue() > 150  # the picture across the middle
    turned = thumbs.story_frame(img, 1, Story(1.0, 0.0, 0.0), 90, 160)
    assert turned.pixelColor(45, 10).red() > 150 and turned.pixelColor(45, 150).blue() > 150  # one clockwise turn
    assert thumbs.cut_crop(QImage(), (0, 0, 1, 1)).isNull()


def test_widgets_survive_a_hostile_provider_for_a_derived_row():
    cat = Catalog(_pics(2))
    band = dv.Band()
    band.add(0, dv.CROP, crop=CROP)
    s = Filmstrip(cat, _Holds(2, _img()), band=band)
    s.rotation_of = lambda i: 1 / 0
    s.resize(600, 120)
    s.show()
    QApplication.processEvents()
    assert s.viewport().grab().toImage().width() > 0
    assert s.context_menu_for(QPoint(5000, 5)) is None
    s.shutdown()


def test_peaking_and_the_af_point_belong_to_the_source_frame_not_to_a_derived_picture():
    features.set_mode(features.ADVANCED)
    win = make_window(marked=())
    try:
        assert win._peak_image(0) is not None and not win._derived_view()
        crop_with(win)
        assert win._derived_view() and win._peak_image(0) is None and win._focus_frac(0) is None
        key(win, Qt.Key_X)  # an edit shows the source's own pixels again
        assert not win._derived_view() and win._peak_image(0) is not None
        key(win, Qt.Key_Escape)
        assert win._derived_view()
    finally:
        win.close()


# -- the fallbacks, forced ------------------------------------------------------------------------------------------
def test_x_on_a_derived_picture_whose_source_is_not_decoded_says_so_and_starts_nothing(w, monkeypatch):
    crop_with(w)
    monkeypatch.setattr(w.prefetcher, "cached", lambda i: None)  # the source left the prefetch window
    key(w, Qt.Key_X)
    assert w._edit is None and not w._edit_view and w.last_message.startswith("The original is not decoded")
    assert len(w.band) == 1


def test_a_refused_band_change_says_so_and_leaves_the_band_alone(w, monkeypatch):
    shown(w)
    key(w, Qt.Key_X)
    w._crop_overlay.set_rect_frac(CROP)

    def refuse(*a, **kw):
        raise ValueError("refused")

    monkeypatch.setattr(w.band, "add", refuse)
    key(w, Qt.Key_Return)
    assert not w.band and w._edit is None and w.last_message.startswith("Cannot crop P0")
    monkeypatch.undo()
    story_with(w)
    (d,) = w.band.items
    key(w, Qt.Key_S)
    monkeypatch.setattr(w.band, "add", refuse)
    w.band.remove(d.id)  # the item under a re-fit is gone: replace() refuses, add() refuses
    key(w, Qt.Key_Return)
    assert not w.band and w.last_message.startswith("Cannot fit P0") and w._cur_id is None


def test_a_re_crop_of_a_picture_removed_meanwhile_adds_a_new_one(w):
    crop_with(w)
    (d,) = w.band.items
    key(w, Qt.Key_X)
    w.band.remove(d.id)
    w._crop_overlay.set_rect_frac(CROP2)
    key(w, Qt.Key_Return)
    (n,) = w.band.items
    assert n.id != d.id and n.crop == CROP2 and w._cur_id == n.id


def test_the_viewer_keeps_the_picture_when_a_crop_cannot_be_cut():
    from spuza.ui.viewer import Viewer

    v = Viewer()
    v._derived = dv.Derived(1, 0, dv.CROP, crop=CROP)
    null = QImage()
    assert v._cut(null).isNull()
    assert v._cut(_img()).width() < 64
    v._derived = None
    assert v._cut(_img()).width() == 64
    v.shutdown()
