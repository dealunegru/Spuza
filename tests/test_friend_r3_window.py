"""Friend round 3, window side: Mark all / Unmark all with one level of undo, the two rotate controls, the
"Write rotation to copies (EXIF)" Preferences row and the wiring the other views rely on. Synthetic pictures only;
the views' own painting is covered beside them, so the views here are recorded, not inspected."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from spuza import features
from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui import settings
from spuza.ui.window import MainWindow

ROOT = Path("/cards/X")
CTRL = Qt.KeyboardModifier.ControlModifier
CTRL_SHIFT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier


def loader(path, max_side):
    return Image.new("RGB", (64, 48), (90, 120, 200)), PreviewInfo(path, "pillow", 64, 48, 10, 1.0)


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond()


def make_window(n=6, marked=(1, 3, 5), **kw):
    pics = [Picture(f"P{i}", ROOT / f"P{i}.JPG", None, (ROOT / f"P{i}.JPG",), "JPG", 1_000_000, 1.0)
            for i in range(n)]
    cat = Catalog(pics)
    win = MainWindow(cat, Prefetcher(cat, loader=loader), ROOT, **kw)
    win.resize(1000, 700)
    win.show()
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 3000)
    if n:
        pump(lambda: win.viewer.has_image)
    for i in marked:
        cat.set_mark(i, True)
    win.refresh_marks()
    return win


@pytest.fixture
def w():
    win = make_window()
    yield win
    win.close()


@pytest.fixture
def wa():
    features.set_mode(features.ADVANCED)
    win = make_window()
    yield win
    win.close()


def marks(win):
    return {i for i in range(len(win.catalog)) if win.catalog.is_marked(i)}


def label(win):
    return win.buttons["markall"].label


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, i):
        self.calls.append(i)


def record_views(win):
    recs = {}
    for name in ("viewer", "filmstrip", "review", "compare"):
        v = getattr(win, name)
        if v is not None:
            recs[name] = Recorder()
            v.rotation_changed = recs[name]
    return recs


# -- mark all / unmark all ---------------------------------------------------------------------------------
def test_mark_all_marks_everything_and_the_label_flips(w):
    assert label(w) == "Mark all"
    w.buttons["markall"].click()
    assert marks(w) == set(range(6)) and label(w) == "Unmark all"
    assert w.last_message.startswith("Marked 3 more, 6 in all")  # 3 were marked already: the count that changed
    assert w.sidebar.counts()["Marked"] == 6
    w.buttons["markall"].click()
    assert marks(w) == set() and label(w) == "Mark all"
    assert w.last_message.startswith("Unmarked 6")


def test_mark_all_from_nothing_says_the_total(w):
    for i in (1, 3, 5):
        w.catalog.set_mark(i, False)
    w.mark_all(True)
    assert w.last_message.startswith("Marked 6 ")


def test_ctrl_a_and_ctrl_shift_a_keys(w):
    QTest.keyClick(w, Qt.Key_A, CTRL)
    assert marks(w) == set(range(6))
    QTest.keyClick(w, Qt.Key_A, CTRL_SHIFT)
    assert marks(w) == set()
    assert w.advance_on_mark is False  # plain A is still Move on mark, untouched by the chords


def test_unmark_all_then_ctrl_z_restores_the_exact_set(w):
    w.catalog.set_mark(0, True)
    before = marks(w)
    w.mark_all(False)
    assert marks(w) == set()
    QTest.keyClick(w, Qt.Key_Z, CTRL)
    assert marks(w) == before and "Undid" in w.last_message and "4 marked again" in w.last_message
    QTest.keyClick(w, Qt.Key_Z, CTRL)  # one level only
    assert w.last_message == "Nothing to undo" and marks(w) == before


def test_undo_after_mark_all_and_nothing_to_undo_at_start(w):
    w.undo_mark_all()
    assert w.last_message == "Nothing to undo"
    w.mark_all(True)
    w.undo_mark_all()
    assert marks(w) == {1, 3, 5} and label(w) == "Mark all"


def test_a_second_mark_all_replaces_the_snapshot(w):
    w.mark_all(False)  # snapshot {1,3,5}
    w.mark_all(True)  # snapshot {} replaces it
    w.undo_mark_all()
    assert marks(w) == set()


def test_start_fresh_drops_the_undo_snapshot(w):
    w.mark_all(False)
    w.start_fresh()
    w.undo_mark_all()
    assert w.last_message == "Nothing to undo"


def test_label_follows_a_manual_mark_that_completes_or_breaks_all(w):
    for i in (0, 2, 4):
        w.catalog.set_mark(i, True)
    w.refresh_marks()
    assert label(w) == "Unmark all"
    w.toggle_mark()  # picture 0 off
    assert label(w) == "Mark all"
    w.toggle_mark()
    assert label(w) == "Unmark all"


def test_no_op_cases_say_so_and_keep_the_snapshot(w):
    w.mark_all(True)
    w.mark_all(True)
    assert w.last_message == "Everything is already marked"
    w.undo_mark_all()
    assert marks(w) == {1, 3, 5}  # the no-op did not overwrite the snapshot


def test_empty_catalog_does_not_throw():
    win = make_window(n=0, marked=())
    try:
        win.mark_all(True)
        assert win.last_message == "No pictures to mark" and label(win) == "Mark all"
        win.rotate(1)
        assert win.rotations == {}
        QTest.keyClick(win, Qt.Key_Z, CTRL)
    finally:
        win.close()


def test_ctrl_a_works_with_the_review_grid_shown(w):
    w.buttons["review"].click()
    assert w.stack.currentIndex() == 1
    QTest.keyClick(w.review.view, Qt.Key_A, CTRL)
    assert marks(w) == set(range(6)) and w.review.model.rowCount() == 6  # the grid lists the newly marked ones
    assert label(w) == "Unmark all"
    QTest.keyClick(w.review.view, Qt.Key_A, CTRL_SHIFT)
    assert marks(w) == set() and w.review.model.rowCount() == 0
    QTest.keyClick(w.review.view, Qt.Key_Z, CTRL)  # Ctrl+Z is the window's undo, not the grid's Z (zoom)
    assert marks(w) == set(range(6)) and w.stack.currentIndex() == 1 and w.review.stack.currentIndex() != 2


def test_ctrl_a_in_the_multi_select_grid_marks_rather_than_selects(wa):
    wa.buttons["review"].click()
    QTest.keyClick(wa.review.view, Qt.Key_A, CTRL)
    assert marks(wa) == set(range(6))


def test_mark_all_button_in_review_refreshes_the_grid(w):
    w.buttons["review"].click()
    w.buttons["markall"].click()
    assert w.review.model.rowCount() == 6 and w.stack.currentIndex() == 1


def test_undo_says_when_hand_marks_since_the_action_are_replaced(w):
    w.mark_all(False)
    w.catalog.set_mark(0, True)
    w.catalog.set_mark(2, True)
    w.undo_mark_all()
    assert marks(w) == {1, 3, 5} and "(2 later changes replaced)" in w.last_message
    w.mark_all(False)
    w.undo_mark_all()
    assert "later" not in w.last_message


def test_every_classic_control_is_reachable_at_the_default_size(w):
    """No button may fall into the overflow chevron at 1280x800. The first row's width is compared with the same
    row's width at f98bb04 (a relative check: the offscreen test font is wider than a real one, 1999 px on the
    base): the new buttons live on their own row, so the first row must not have grown."""
    w.resize(1280, 800)
    QTest.qWait(50)
    first_row = [b for n, b in w.buttons.items() if n not in ("markall", "rotl", "rotr")] + [w.prefs_button]
    assert all(b.parent() is w.toolbar for b in first_row)
    assert all(w.buttons[n].parent() is w.toolbar3 for n in ("markall", "rotl", "rotr"))
    BASE_FIRST_ROW_HINT = 1999  # offscreen sizeHint at f98bb04, measured; real Windows measured 1070
    assert w.toolbar.sizeHint().width() <= BASE_FIRST_ROW_HINT
    assert w.toolbar3.sizeHint().width() < w.toolbar.sizeHint().width()
    assert w.toolbar3.isVisible()


# -- rotation ------------------------------------------------------------------------------------------------
def test_rotate_keys_update_rotations_mod_4_and_notify_every_built_view(w):
    recs = record_views(w)
    assert set(recs) == {"viewer", "filmstrip"}  # review and compare are not built yet
    QTest.keyClick(w, Qt.Key_BracketRight)
    assert w.rotations == {0: 1} and "now 90" in w.last_message
    QTest.keyClick(w, Qt.Key_BracketRight)
    QTest.keyClick(w, Qt.Key_BracketRight)
    assert w.rotations == {0: 3}
    QTest.keyClick(w, Qt.Key_BracketRight)
    assert w.rotations == {} and "upright" in w.last_message  # 0 is omitted
    QTest.keyClick(w, Qt.Key_BracketLeft)
    assert w.rotations == {0: 3} and "left" in w.last_message
    assert recs["viewer"].calls == [0] * 5 and recs["filmstrip"].calls == [0] * 5


def test_rotate_buttons_and_the_views_read_the_window_dict(w):
    w.step(1)
    w.buttons["rotr"].click()
    w.buttons["rotl"].click()
    w.buttons["rotl"].click()
    assert w.rotations == {1: 3}
    for v in (w.viewer, w.filmstrip):
        assert v.rotation_of(1) == 3 and v.rotation_of(0) == 0
    assert (w.buttons["rotl"].cap, w.buttons["rotr"].cap) == ("[", "]")


def test_rotate_in_review_turns_the_selected_pictures(wa):
    wa.buttons["review"].click()
    wa.review.view.selectAll()  # advanced: multi-select; marked 1, 3, 5
    recs = record_views(wa)
    QTest.keyClick(wa.review.view, Qt.Key_BracketRight)
    assert wa.rotations == {1: 1, 3: 1, 5: 1} and "3 pictures" in wa.last_message
    assert recs["review"].calls == [1, 3, 5] and recs["viewer"].calls == [1, 3, 5]
    assert wa.review.rotation_of(3) == 1


def test_rotate_in_classic_review_turns_the_one_selected_picture(w):
    w.buttons["review"].click()
    w.buttons["rotl"].click()
    assert w.rotations == {1: 3} and w.catalog.index == 0


def test_rotate_in_compare_turns_the_focused_picture(wa):
    wa.open_compare()
    assert wa._compare_shown()
    recs = record_views(wa)
    f = wa.compare.focused_index()
    QTest.keyClick(wa.compare, Qt.Key_BracketRight)
    assert wa.rotations == {f: 1} and recs["compare"].calls == [f]
    assert wa.compare.rotation_of(f) == 1


def test_copy_factory_gets_rotations_keyed_by_primary_and_the_pref(w):
    w.rotations.update({0: 1, 1: 3, 2: 2})
    w.catalog.set_mark(0, True)  # 0, 1, 3, 5 marked; 2 is turned but not marked
    pics = w.catalog.marked()
    sheet = w._default_copy_sheet_factory(pics)
    assert sheet.rotations == {pics[0].primary: 1, pics[1].primary: 3}
    assert sheet.write_rotation is True
    settings.write_write_rotation(w.prefs, False)
    assert w._default_copy_sheet_factory(pics).write_rotation is False


def test_resume_controller_shares_the_rotations_dict():
    features.set_mode(features.ADVANCED)
    win = make_window()
    try:
        assert win._ensure_resume().rotations is win.rotations
    finally:
        win.close()


def test_close_calls_the_filmstrips_shutdown(w):
    calls = []
    real = w.filmstrip.shutdown
    w.filmstrip.shutdown = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    w.close()
    assert calls == [1]


# -- the Preferences row -------------------------------------------------------------------------------------
def test_prefs_row_seeds_from_the_saved_value_and_defaults_on(w):
    sheet = w._default_prefs_sheet_factory(5, 3, 2560)
    assert sheet.write_rotation() is True and sheet.rotation_check.text() == "Write rotation to copies (EXIF)"
    settings.write_write_rotation(w.prefs, False)
    sheet = w._default_prefs_sheet_factory(5, 3, 2560)
    assert sheet.write_rotation() is False and sheet.seeded_rotation is False


def test_prefs_row_saves_only_when_flipped(w):
    w.open_prefs()
    w._prefs_sheet.accept()
    assert "copy/rotation" not in settings.open_settings().allKeys()  # an unrelated OK writes nothing
    w.open_prefs()
    w._prefs_sheet.rotation_check.setChecked(False)
    w._prefs_sheet.accept()
    s = settings.open_settings()
    assert settings.read_write_rotation(s) is False and "copy/rotation" in s.allKeys()
    w.open_prefs()
    sheet = w._prefs_sheet
    assert sheet.write_rotation() is False
    sheet.reset_to_defaults()
    assert sheet.write_rotation() is True


def test_settings_round_trip(w):
    s = settings.open_settings()
    assert settings.read_write_rotation(s) is True
    settings.write_write_rotation(s, False)
    assert settings.read_write_rotation(settings.open_settings()) is False
    settings.write_write_rotation(s, True)
    assert settings.read_write_rotation(settings.open_settings()) is True


# -- docs ----------------------------------------------------------------------------------------------------
def test_readme_check_passes_and_names_the_new_keys():
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("check_readme_r3", root / "docs" / "check_readme.py")
    cr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cr)
    text = (root / "README.md").read_text(encoding="utf-8")
    assert cr.check(text, root) == []
    assert {"[", "]", "ctrl+a", "ctrl+shift+a", "ctrl+z"} <= cr.readme_keys(text)
    assert "Write rotation to copies (EXIF)" in text
