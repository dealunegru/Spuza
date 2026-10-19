"""Friend round 5, window side: X crops, S story-fits (since round 7 each Enter makes a derived picture on the
filmstrip, see test_friend_r7_band); the marks on the filmstrip and in the inspector; the session; the copy sheet.
Synthetic pictures only (offscreen)."""

from __future__ import annotations

import json
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from spuza import derived as dv
from spuza import features
from spuza.session import SessionStore, card_key
from spuza.story import Story
from spuza.ui.resume import ResumeController
from test_friend_r3_window import make_window, pump
from test_ui_resume import make_card, open_card

CROP = (0.1, 0.2, 0.6, 0.7)


@pytest.fixture
def w():
    win = make_window(marked=())
    yield win
    win.close()


def key(win, k):
    QTest.keyClick(win, k)


def shown(win):
    """Wait until the current picture is decoded and on screen: X and S refuse without one (slow machines)."""
    pump(lambda: win.prefetcher.cached(win.catalog.index) is not None and win.viewer.has_image)


def draw(win, rect=CROP):
    """What a drag does: the overlay's rectangle (its mouse handling is covered with the overlay itself)."""
    win._crop_overlay.set_rect_frac(rect)


def crop_with(win, rect=CROP, i=None):
    shown(win)
    key(win, Qt.Key_X)
    draw(win, rect)
    key(win, Qt.Key_Return)


def story_with(win, story=Story(2.0, 0.0, 0.0)):
    shown(win)
    key(win, Qt.Key_S)
    win._story_editor._story = story
    key(win, Qt.Key_Return)


def crops(win):
    return [d.crop for d in win.band if d.kind == dv.CROP]


def stories(win):
    return [d.story for d in win.band if d.kind == dv.STORY]


# -- crop ---------------------------------------------------------------------------------------------------
def test_overlays_are_lazy(w):
    assert w._crop_overlay is None and w._story_editor is None
    for _ in range(3):
        w.step(1)
    assert w._crop_overlay is None and w._story_editor is None  # no crop anywhere: stepping builds nothing


def test_x_enters_crop_mode_at_fit_and_enter_applies(w):
    w.viewer.toggle_zoom()
    assert w.viewer.zoomed
    key(w, Qt.Key_X)
    assert w._crop_overlay.editing and not w.viewer.zoomed
    draw(w)
    key(w, Qt.Key_Return)
    assert not w._crop_overlay.editing
    assert crops(w) == [CROP] and w._cur_id == w.band.items[0].id
    assert w._crop_overlay._applied is None  # the original carries no outline; the derived picture shows the crop


def test_enter_with_no_rectangle_changes_nothing(w):
    crop_with(w)
    key(w, Qt.Key_X)
    assert w._crop_overlay._rect == CROP  # begins from the derived crop's rectangle
    w._crop_overlay.set_rect_frac(None)
    key(w, Qt.Key_Return)
    assert crops(w) == [CROP] and len(w.band) == 1


def test_escape_cancels_and_keeps_the_derived_crop(w):
    crop_with(w)
    key(w, Qt.Key_X)
    draw(w, (0.0, 0.0, 0.3, 0.3))
    key(w, Qt.Key_Escape)
    assert not w._crop_overlay.editing and crops(w) == [CROP]


def test_escape_in_crop_mode_does_not_leave_full_bleed_or_review(w):
    w.set_bleed(True)
    key(w, Qt.Key_X)
    key(w, Qt.Key_Escape)
    assert w.is_bleed and not w._crop_overlay.editing
    key(w, Qt.Key_Escape)  # now it is the plain Esc: leaves full-bleed
    assert not w.is_bleed


def test_x_again_in_the_edit_does_nothing(w):
    crop_with(w)
    key(w, Qt.Key_X)
    key(w, Qt.Key_X)
    assert w._crop_overlay.editing and crops(w) == [CROP]


def test_backspace_removes_the_derived_picture_out_of_the_mode_and_clears_the_rectangle_in_it(w):
    crop_with(w)
    key(w, Qt.Key_Backspace)  # out of the mode: the derived picture goes
    assert crops(w) == [] and w._cur_id is None and w.catalog.index == 0
    crop_with(w)
    key(w, Qt.Key_X)
    key(w, Qt.Key_Backspace)  # in the mode: the rectangle goes, the picture stays
    assert w._crop_overlay.editing and w._crop_overlay.rect_frac() is None and len(w.band) == 1


def test_zoom_keys_do_nothing_while_cropping(w):
    key(w, Qt.Key_X)
    for k in (Qt.Key_Z, Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus):
        key(w, k)
    assert not w.viewer.zoomed and w._crop_overlay.editing
    w.buttons["zoom"].click()
    assert not w.viewer.zoomed and not w.buttons["zoom"].isChecked()


def test_marks_keep_working_while_editing(w):
    key(w, Qt.Key_X)
    key(w, Qt.Key_Space)
    assert w.catalog.is_marked(0) and w._crop_overlay.editing


@pytest.mark.parametrize("nav,expect", [(Qt.Key_Right, 1), (Qt.Key_J, 1)])
def test_navigation_cancels_the_edit_then_moves(w, nav, expect):
    crop_with(w)
    key(w, Qt.Key_X)
    draw(w, (0.0, 0.0, 0.3, 0.3))
    key(w, nav)
    assert w.catalog.index == expect and not w._crop_overlay.editing
    assert crops(w) == [CROP]  # the cancelled edit left the derived crop as it was


def test_navigation_back_cancels_a_story_edit(w):
    w.step(1)
    shown(w)
    key(w, Qt.Key_S)
    key(w, Qt.Key_K)
    assert w.catalog.index == 0 and not w._story_editor.active and not w.band


def test_the_crop_follows_the_row_not_an_outline(w):
    crop_with(w)
    pm = w.viewer.image_item().pixmap()
    assert (pm.width(), pm.height()) != (64, 48)  # the derived row shows the crop
    w.step(1)
    pm = w.viewer.image_item().pixmap()
    assert w.catalog.index == 1 and (pm.width(), pm.height()) == (64, 48)  # picture 1 has none
    w.step(-1)
    assert w.catalog.index == 0 and w._cur_id == w.band.items[0].id
    w.filmstrip.picked.emit(2)
    assert w.catalog.index == 2 and w._cur_id is None


def test_the_crop_follows_a_pixmap_swap(w):
    crop_with(w)
    from PySide6.QtGui import QColor, QImage

    im = QImage(128, 96, QImage.Format.Format_RGB888)
    im.fill(QColor("#224466"))
    w.viewer._put(im, keep_centre=False)  # a full decode arriving: the crop of it is what is shown
    pm = w.viewer.image_item().pixmap()
    assert (pm.width(), pm.height()) == (64, 48)  # CROP of 128 x 96
    assert w._crop_overlay._size == (64.0, 48.0)


def test_crop_keys_are_ignored_on_the_review_page(w):
    w.catalog.set_mark(0, True)
    w.open_review()
    assert w.stack.currentIndex() == 1
    for k in (Qt.Key_X, Qt.Key_S, Qt.Key_Backspace):
        key(w, k)
    assert w._crop_overlay is None and w._story_editor is None and not w.band


def test_crop_keys_are_ignored_in_compare():
    features.set_mode(features.ADVANCED)
    win = make_window(marked=())
    try:
        win.open_compare()
        assert win._compare_shown()
        for k in (Qt.Key_X, Qt.Key_S, Qt.Key_Backspace):
            QTest.keyClick(win, k)
        assert win._crop_overlay is None and win._story_editor is None
    finally:
        win.close()


def test_x_is_ignored_while_story_editing_and_s_while_cropping(w):
    key(w, Qt.Key_S)
    key(w, Qt.Key_X)
    assert w._crop_overlay is None and w._story_editor.active
    key(w, Qt.Key_Escape)
    key(w, Qt.Key_X)
    key(w, Qt.Key_S)
    assert not w._story_editor.active and w._crop_overlay.editing


# -- story --------------------------------------------------------------------------------------------------
def test_s_enters_story_mode_and_enter_applies(w):
    w.viewer.toggle_zoom()
    key(w, Qt.Key_S)
    assert w._story_editor.active and not w.viewer.zoomed
    w._story_editor._story = Story(1.5, 0.0, 0.0)
    key(w, Qt.Key_Return)
    assert not w._story_editor.active
    assert stories(w) == [Story(1.5, 0.0, 0.0)] and w._cur_id == w.band.items[0].id


def test_story_begins_from_the_existing_story_and_turns_and_native_size(w):
    story_with(w, Story(1.2, 0.0, 0.0))
    w.rotations[0] = 1
    key(w, Qt.Key_S)  # on the derived story: a re-fit, starting from its story
    assert w._story_editor.story().zoom == pytest.approx(1.2)
    assert w._story_editor._src == (48, 64)  # turned once: the loader's 64x48 becomes 48x64
    key(w, Qt.Key_Escape)


def test_story_escape_cancels_and_keeps_the_old_one(w):
    story_with(w)
    key(w, Qt.Key_S)
    w._story_editor._story = Story(3.0, 0.0, 0.0)
    key(w, Qt.Key_Escape)
    assert not w._story_editor.active and stories(w) == [Story(2.0, 0.0, 0.0)]


def test_story_s_again_does_nothing_and_backspace_resets_the_frame(w):
    story_with(w)
    key(w, Qt.Key_S)
    key(w, Qt.Key_S)
    assert w._story_editor.active and stories(w) == [Story(2.0, 0.0, 0.0)]
    key(w, Qt.Key_Backspace)
    assert w._story_editor.active and w._story_editor.story() == Story() and len(w.band) == 1


def test_story_zoom_keys_go_to_the_editor_not_the_viewer(w):
    key(w, Qt.Key_S)
    z0 = w._story_editor.story().zoom
    key(w, Qt.Key_Plus)
    z1 = w._story_editor.story().zoom
    assert z1 > z0 and not w.viewer.zoomed
    key(w, Qt.Key_Equal)
    assert w._story_editor.story().zoom > z1
    key(w, Qt.Key_Minus)
    key(w, Qt.Key_0)
    assert w._story_editor.story() == Story()
    key(w, Qt.Key_Z)
    assert not w.viewer.zoomed and w._story_editor.active


def test_plus_minus_zoom_the_viewer_outside_story_mode(w):
    calls = []
    w.viewer.zoom_in = lambda: calls.append("in")
    w.viewer.zoom_out = lambda: calls.append("out")
    key(w, Qt.Key_Plus)
    key(w, Qt.Key_Minus)
    assert calls == ["in", "out"]
    key(w, Qt.Key_0)  # nothing: not a story
    assert w._story_editor is None


def test_a_crop_and_a_story_are_two_derived_pictures_of_one_original(w):
    story_with(w)
    w.go_to(0)
    crop_with(w)
    assert [d.kind for d in w.band] == [dv.STORY, dv.CROP] and {d.source for d in w.band} == {0}
    w.go_to(0)
    story_with(w, Story(1.5, 0.0, 0.0))
    assert stories(w) == [Story(2.0, 0.0, 0.0), Story(1.5, 0.0, 0.0)] and crops(w) == [CROP]


# -- indicators ---------------------------------------------------------------------------------------------
def test_filmstrip_tags_distinguish_crop_and_story(w):
    assert [w.filmstrip.row_tags(i) for i in range(3)] == [[], [], []]
    crop_with(w)
    w.go_to(1)
    story_with(w)
    crop_id, story_id = (d.id for d in w.band)
    assert w.filmstrip.row_tags(0) == [] and w.filmstrip.row_tags(1) == []  # the originals carry none
    assert w.filmstrip.row_tags(0, crop_id) == [("crop", "crop")]
    assert w.filmstrip.row_tags(1, story_id) == [("9:16", "story")]
    cell = w.filmstrip.visualRect(w.filmstrip.model().index(1))
    assert w.filmstrip.viewport().grab(cell).toImage().width() > 0  # paints without raising


def test_inspector_line(w):
    assert not any(t.startswith(("Crop", "Story")) for t in w.inspector.lines)
    crop_with(w)
    assert "Crop of P0.JPG" in w.inspector.lines and "32 x 24 px" in w.inspector.lines  # pixels of the 64 x 48 stand-in
    w.go_to(0)
    story_with(w)
    assert "Story fit of P0.JPG" in w.inspector.lines
    w.step(1)  # past the story: picture 1
    assert not any(t.startswith(("Crop", "Story")) for t in w.inspector.lines)
    w.go_to(0, w.band.items[0].id)
    assert w.inspector.lines[-1] in ("Marked", "Not marked") and w.inspector.lines[-4].startswith("Crop of")


# -- copy ---------------------------------------------------------------------------------------------------
def test_copy_factory_gets_the_derived_pairs():
    win = make_window(marked=(0, 1, 3))
    try:
        a = win.band.add(0, dv.CROP, crop=CROP)
        b = win.band.add(1, dv.STORY, story=Story(1.4, 0.0, 0.0))
        win.band.add(2, dv.CROP, crop=(0.0, 0.0, 0.5, 0.5), marked=False)  # unmarked: not copied
        pics = win.catalog.marked()  # 0, 1, 3
        sheet = win._default_copy_sheet_factory(pics, derived=win._derived_pairs())
        assert sheet.derived == [(win.catalog.pictures[0], a), (win.catalog.pictures[1], b)]
    finally:
        win.close()


# -- the session --------------------------------------------------------------------------------------------
def _saved(tmp_path, n=6):
    root = make_card(tmp_path, n)
    cat = open_card(root)
    return root, cat, SessionStore(tmp_path / "s"), card_key(root, cat.pictures)


def test_session_round_trip_keeps_crops_and_stories(tmp_path):
    root, cat, store, key_ = _saved(tmp_path)
    store.save(key_, root, cat.pictures, {1}, 2, crops={0: CROP}, stories={3: Story(1.5, 0.25, -0.5)})
    r = SessionStore(tmp_path / "s").load(key_, root, cat.pictures)
    assert r.crops == {0: CROP} and r.stories == {3: (1.5, 0.25, -0.5)}
    assert r.marks == {1} and r.index == 2


def test_only_a_crop_is_worth_restoring(tmp_path):
    root, cat, store, key_ = _saved(tmp_path)
    store.save(key_, root, cat.pictures, set(), 0, stories={2: Story(2.0, 0.0, 0.0)})
    assert store.load(key_, root, cat.pictures).stories == {2: (2.0, 0.0, 0.0)}


def test_an_older_session_without_the_fields_still_loads(tmp_path):
    root, cat, store, key_ = _saved(tmp_path)
    store.save(key_, root, cat.pictures, {1}, 2, rotations={0: 1}, crops={0: CROP})
    path = store._path(key_)
    doc = json.loads(path.read_text())
    del doc["crops"], doc["stories"]
    path.write_text(json.dumps(doc))
    r = store.load(key_, root, cat.pictures)
    assert r.marks == {1} and r.rotations == {0: 1} and r.crops == {} and r.stories == {}


def test_crop_rows_are_validated_like_the_turned_rows(tmp_path):
    root, cat, store, key_ = _saved(tmp_path)
    store.save(key_, root, cat.pictures, {1}, 0, crops={0: CROP, 2: CROP, 3: CROP}, stories={4: Story(), 5: Story()})
    path = store._path(key_)
    doc = json.loads(path.read_text())
    doc["crops"][0]["size"] += 1  # picture 0 changed since
    doc["crops"][1]["mtime"] += 3600  # picture 2 re-dated
    doc["crops"].append({"name": "gone.JPG", "size": 1, "mtime": 1.0, "rect": list(CROP)})
    doc["stories"][0]["size"] += 1
    path.write_text(json.dumps(doc))
    r = store.load(key_, root, cat.pictures)
    assert r.crops == {3: CROP} and r.stories == {5: (1.0, 0.0, 0.0)}


@pytest.mark.parametrize("junk", [None, 5, "x", {}, [1, "a", None], [{"name": 1}], [[]], [{"rect": 1}]])
def test_hostile_crop_fields_restore_the_marks_anyway(tmp_path, junk):
    root, cat, store, key_ = _saved(tmp_path)
    store.save(key_, root, cat.pictures, {1}, 0, crops={0: CROP})
    path = store._path(key_)
    doc = json.loads(path.read_text())
    doc["crops"] = doc["stories"] = junk
    path.write_text(json.dumps(doc))
    r = store.load(key_, root, cat.pictures)
    assert r is not None and r.marks == {1} and r.crops == {} and r.stories == {}


def test_hostile_values_inside_rows_are_dropped(tmp_path):
    root, cat, store, key_ = _saved(tmp_path)
    store.save(key_, root, cat.pictures, {1}, 0, crops={0: CROP, 1: CROP, 2: CROP, 3: CROP},
               stories={4: Story(), 5: Story()})
    path = store._path(key_)
    doc = json.loads(path.read_text())
    doc["crops"][0]["rect"] = [0, 0, float("nan"), 1]
    doc["crops"][1]["rect"] = [0.5, 0.5, 0.5, 0.9]  # no width
    doc["crops"][2]["rect"] = [0, 0, 2, 1]  # out of 0..1
    doc["stories"][0]["zoom"] = 0.2  # below fit
    doc["stories"][1]["dx"] = True
    path.write_text(json.dumps(doc))
    r = store.load(key_, root, cat.pictures)
    assert r.crops == {3: CROP} and r.stories == {}


def test_deeply_nested_crop_field_is_one_clean_none(tmp_path):
    root, cat, store, key_ = _saved(tmp_path)
    store.save(key_, root, cat.pictures, {1}, 0)
    path = store._path(key_)
    text = path.read_text()
    old = sys.getrecursionlimit()
    path.write_text(text[:-1] + ',"crops":' + "[" * 5000 + "]" * 5000 + "}")
    r = store.load(key_, root, cat.pictures)  # refused or parsed, never raised
    assert r is None or (r.crops == {} and r.marks == {1})
    assert sys.getrecursionlimit() == old


def test_save_skips_bad_crop_and_story_values(tmp_path):
    root, cat, store, key_ = _saved(tmp_path)
    store.save(key_, root, cat.pictures, {1}, 0,
               crops={0: "x", 1: (0, 0, 1), 2: (0, 0, float("nan"), 1), 99: CROP, 3: CROP},
               stories={0: "x", 1: Story(float("inf"), 0, 0), 2: Story(0.5, 0, 0), 4: Story(1.2, 0, 0)})
    r = store.load(key_, root, cat.pictures)
    assert r.crops == {3: CROP} and r.stories == {4: (1.2, 0.0, 0.0)}


def test_resume_controller_saves_and_restores_the_shared_dicts(tmp_path):
    features.set_mode(features.ADVANCED)
    root = make_card(tmp_path, 6)
    store = SessionStore(tmp_path / "s")
    cat = open_card(root)
    crops, stories = {}, {}
    c = ResumeController(cat, root, store, interval_ms=60000, crops=crops, stories=stories)
    assert c.crops is crops and c.stories is stories
    crops[2] = CROP
    stories[4] = Story(1.5, 0.0, 0.25)
    c.flush()
    cat2 = open_card(root)
    crops2, stories2 = {7: CROP}, {}
    c2 = ResumeController(cat2, root, store, interval_ms=60000, crops=crops2, stories=stories2)
    banner = c2.restore()
    assert crops2 == {2: CROP} and stories2 == {4: Story(1.5, 0.0, 0.25)}
    assert "1 crop" in banner and "1 story fit" in banner


def test_resume_change_detection_sees_a_crop(tmp_path):
    features.set_mode(features.ADVANCED)
    root = make_card(tmp_path, 4)
    calls = []

    class Spy(SessionStore):
        def save(self, *a, **kw):
            calls.append(kw)
            super().save(*a, **kw)

    c = ResumeController(open_card(root), root, Spy(tmp_path / "s"), interval_ms=60000, crops={}, stories={})
    c.tick()
    assert c.wait_idle() and calls == []
    c.crops[1] = CROP
    c.tick()
    assert c.wait_idle() and len(calls) == 1 and calls[0]["crops"] == {1: CROP}
    c.tick()
    assert c.wait_idle() and len(calls) == 1


def test_start_fresh_keeps_crops_like_rotations(tmp_path):
    features.set_mode(features.ADVANCED)
    root = make_card(tmp_path, 4)
    store = SessionStore(tmp_path / "s")
    crops = {2: CROP}
    c = ResumeController(open_card(root), root, store, interval_ms=60000, crops=crops, stories={})
    c.start_fresh()
    c.tick()
    assert c.wait_idle()
    crops2 = {}
    ResumeController(open_card(root), root, store, interval_ms=60000, crops=crops2, stories={}).restore()
    assert crops2 == {2: CROP}


def test_window_shares_its_band_with_the_resume_controller(tmp_path, monkeypatch):
    features.set_mode(features.ADVANCED)
    monkeypatch.setenv("SPUZA_SESSIONS_DIR", str(tmp_path / "sessions"))
    win = make_window(marked=())
    try:
        ctl = win._ensure_resume()
        assert ctl.band is win.band and ctl.rotations is win.rotations
    finally:
        win.close()


def test_a_saved_derived_picture_comes_back_on_the_filmstrip(tmp_path, monkeypatch):
    features.set_mode(features.ADVANCED)
    monkeypatch.setenv("SPUZA_SESSIONS_DIR", str(tmp_path / "sessions"))
    win = make_window(marked=())
    try:
        crop_with(win)
        win._ensure_resume().flush()
    finally:
        win.close()
    win2 = make_window(marked=())
    try:
        assert [d.crop for d in win2.band] == [CROP]
        assert win2.filmstrip.model().rowCount() == 7 and win2.sidebar.counts()["All"] == 7
    finally:
        win2.close()


# -- turning during an edit ---------------------------------------------------------------------------------
def test_turning_during_a_story_edit_rebegins_the_editor_with_the_new_turns(w):
    key(w, Qt.Key_S)
    assert w._story_editor._src == (64, 48)
    QTest.keyClick(w, Qt.Key_BracketRight)
    assert w.rotations == {0: 1} and w._edit == "story" and w._story_editor.active
    assert w._story_editor._src == (48, 64)  # the editor works in the new turned frame
    w._story_editor._story = Story(1.3, 0.0, 0.0)
    key(w, Qt.Key_Return)
    assert stories(w)[0].zoom == pytest.approx(1.3)


def test_turning_clamps_a_derived_story_for_the_new_frame(w):
    w.band.add(0, dv.STORY, story=Story(2.0, 0.0, 0.0))  # fine for 64x48 (zoom limit 2.25), above the 1.33 limit once turned
    QTest.keyClick(w, Qt.Key_BracketRight)
    assert stories(w)[0].zoom <= 1.34


def test_turning_during_a_crop_edit_keeps_the_rectangle(w):
    key(w, Qt.Key_X)
    draw(w)
    QTest.keyClick(w, Qt.Key_BracketLeft)
    assert w._edit == "crop" and w._crop_overlay.editing and w._crop_overlay.rect_frac() == CROP
    key(w, Qt.Key_Return)
    assert crops(w) == [CROP]
