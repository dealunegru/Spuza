"""Design round 2: the menu bar (File / Edit / View / Picture / Pills / Help) and the `?` shortcut overlay.

Both are built from ui/actions.py. Keys stay dispatched by keyPressEvent and the QShortcuts; the menu QActions only
display them, so one key press must be one call. Real QTest key events over a window on tiny JPEGs in tmp_path."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from spuza import features
from spuza.catalog import Catalog, Prefetcher, scan
from spuza.ui import actions as act
from spuza.ui.window import MainWindow

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_readme_for_menu", ROOT / "docs" / "check_readme.py")
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)

CTRL, SHIFT = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier
NONE = Qt.KeyboardModifier.NoModifier


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond(), "condition not reached in time"


def make_window(folder: Path, n: int) -> MainWindow:
    folder.mkdir(exist_ok=True)
    for i in range(n):
        Image.new("RGB", (64, 48), (40 * i + 30, 90, 160)).save(folder / f"P{i}.jpg")
    cat = Catalog(scan(folder))
    w = MainWindow(cat, Prefetcher(cat), folder)
    w.resize(1200, 800)
    w.show()
    if n:
        pump(lambda: w.viewer.has_image)
    return w


@pytest.fixture(autouse=True)
def isolated_pills(tmp_path, monkeypatch):
    """The window loads and saves pills in app data: never the real one."""
    monkeypatch.setenv("SPUZA_PILLS_DIR", str(tmp_path / "pills"))


@pytest.fixture
def win(tmp_path):
    w = make_window(tmp_path / "card", 3)
    yield w
    w.close()


@pytest.fixture
def empty_win(tmp_path):
    w = make_window(tmp_path / "none", 0)
    yield w
    w.close()


class Counter:
    def __init__(self):
        self.n = 0

    def __call__(self, *a, **k):
        self.n += 1


def watch(monkeypatch, obj, name) -> Counter:
    c = Counter()
    monkeypatch.setattr(obj, name, c)
    return c


# -- the table ------------------------------------------------------------------------------------
def test_menu_bar_has_the_six_menus_in_order(win):  # 0.12 added Pills, between Picture and Help
    assert [a.text() for a in win.menuBar().actions()] == list(act.MENUS) == ["File", "Edit", "View", "Picture",
                                                                              "Pills", "Help"]


def test_every_action_is_in_its_menu_with_keys(win):
    menus = {a.text(): a.menu() for a in win.menuBar().actions()}
    assert list(menus) == list(act.MENUS)
    ids = [a.id for a in act.ACTIONS]
    assert len(ids) == len(set(ids)) and set(win.menu_actions) == set(ids)
    for a in act.ACTIONS:
        qa = win.menu_actions[a.id]
        assert a.keys and all(a.keys), a.id
        assert qa in menus[a.menu].actions(), a.id
        assert qa.text() == a.label and qa.isCheckable() == a.checkable
        assert [s.toString() for s in qa.shortcuts()] == [QKeySequence(k).toString() for k in a.keys]
        assert qa.shortcutContext() == Qt.ShortcutContext.WidgetShortcut
        assert callable(getattr(win, a.handler)), a.handler
        assert a.group in act.GROUPS


def test_the_menu_lists_the_actions_the_issue_names(win):
    by_menu = {}
    for a in act.ACTIONS:
        by_menu.setdefault(a.menu, []).append(a.label)
    assert by_menu["File"] == ["Copy…", "Preferences…", "Quit"]
    assert by_menu["Edit"] == ["Mark", "Move on mark", "Mark all", "Unmark all", "Undo mark all", "Start fresh"]
    assert by_menu["View"][:6] == ["Back", "Next", "Zoom 100%", "Zoom in", "Zoom out", "Full screen"]
    assert by_menu["Picture"] == ["Rotate left", "Rotate right", "Crop", "Story fit", "Remove crop / story"]
    assert by_menu["Pills"] == [f"Pill {n}" for n in range(1, 10)] + ["Clear all pills", "Manage pills…"]
    assert by_menu["Help"] == ["Keyboard shortcuts"]


def test_every_key_in_the_code_has_an_action():
    """keyPressEvent's keys and the QShortcuts, read from the source the way check_readme does, vs the table."""
    import ast

    tree = ast.parse((ROOT / "src" / "spuza" / "ui" / "window.py").read_text(encoding="utf-8"))
    code = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == "MainWindow":
            for f in n.body:
                if isinstance(f, ast.FunctionDef) and f.name == "keyPressEvent":
                    code |= cr._key_attrs(f)
    code |= cr._sequences(tree)
    table = {cr._seq_key(k) for a in act.ACTIONS for k in a.keys}
    # modes' own keys, listed in the overlay as CONTEXT_KEYS rather than as menu actions
    context = {"esc"}
    assert code - table - context == set(), code - table - context
    assert "esc" in {cr._readme_key(k) for _g, k, _w in act.CONTEXT_KEYS}


# -- one press, one call --------------------------------------------------------------------------
KEYS = [  # (key, modifiers, window attribute counted)
    (Qt.Key_Space, NONE, "toggle_mark"), (Qt.Key_M, NONE, "toggle_mark"), (Qt.Key_Left, NONE, "step"),
    (Qt.Key_K, NONE, "step"), (Qt.Key_Right, NONE, "step"), (Qt.Key_A, CTRL, "mark_all"),
    (Qt.Key_A, CTRL | SHIFT, "mark_all"), (Qt.Key_Z, CTRL, "undo_mark_all"), (Qt.Key_BracketLeft, NONE, "rotate"),
    (Qt.Key_BracketRight, NONE, "rotate"), (Qt.Key_A, NONE, "set_advance_on_mark"),
    (Qt.Key_F, NONE, "set_bleed"), (Qt.Key_R, NONE, "toggle_review"), (Qt.Key_C, NONE, "open_copy"),
    (Qt.Key_Comma, CTRL, "open_prefs"), (Qt.Key_Q, CTRL, "close"), (Qt.Key_Question, SHIFT, "toggle_shortcuts"),
    (Qt.Key_X, NONE, "start_crop"), (Qt.Key_S, NONE, "start_story"), (Qt.Key_Backspace, NONE, "remove_derived"),
]
VIEWER_KEYS = [(Qt.Key_Z, NONE, "toggle_zoom"), (Qt.Key_Plus, NONE, "zoom_in"), (Qt.Key_Minus, NONE, "zoom_out")]


def press_counts(monkeypatch, win, key, mods, attr):
    owner = win.viewer if attr in ("toggle_zoom", "zoom_in", "zoom_out") else win
    handler = watch(monkeypatch, owner, attr)
    ran = watch(monkeypatch, win, "_run_action")
    QTest.keyClick(win, key, mods)
    return handler.n, ran.n


@pytest.mark.parametrize("key,mods,attr", KEYS + VIEWER_KEYS)
def test_one_press_on_the_viewer_is_one_call(monkeypatch, win, key, mods, attr):
    win.setFocus()
    calls, via_menu = press_counts(monkeypatch, win, key, mods, attr)
    assert (calls, via_menu) == (1, 0)


REVIEW_KEYS = [k for k in KEYS if k[2] in ("mark_all", "undo_mark_all", "rotate", "open_prefs", "close",
                                           "toggle_shortcuts")]


@pytest.mark.parametrize("key,mods,attr", REVIEW_KEYS)
def test_one_press_with_the_review_grid_focused_is_one_call(monkeypatch, win, key, mods, attr):
    win.open_review()
    pump(lambda: win.stack.currentIndex() == 1)
    assert win.review.view.hasFocus() or win.review.isAncestorOf(win.focusWidget())
    calls, via_menu = press_counts(monkeypatch, win, key, mods, attr)
    assert (calls, via_menu) == (1, 0)


def test_space_and_arrows_in_the_review_never_reach_the_menu(monkeypatch, win):
    win.catalog.toggle_mark(0)
    win.catalog.toggle_mark(1)
    win.open_review()
    ran = watch(monkeypatch, win, "_run_action")
    for k in (Qt.Key_Space, Qt.Key_Right, Qt.Key_Left, Qt.Key_Z, Qt.Key_Plus):
        QTest.keyClick(win.focusWidget() or win, k)
    assert ran.n == 0


# -- one trigger, one call ------------------------------------------------------------------------
@pytest.mark.parametrize("a", act.ACTIONS, ids=lambda a: a.id)
def test_menu_trigger_calls_the_handler_once(monkeypatch, win, a):
    features.set_mode(features.ADVANCED)
    win.set_advanced(True)
    if a.id in act.PILL_DIGITS:  # a digit's row is hidden until a pill owns it
        while len(win.pill_book.pills()) < act.PILL_DIGITS[a.id]:
            win.pill_book.create(f"Pill {len(win.pill_book.pills()) + 1}", "Blue")
        win.refresh_actions()
    elif a.id == "pill_clear":
        win.pill_book.create("Hero", "Blue")
        win.refresh_actions()
    h = watch(monkeypatch, win, a.handler)
    qa = win.menu_actions[a.id]
    assert qa.isEnabled() and qa.isVisible(), a.id
    qa.trigger()
    assert h.n == 1


def test_mark_from_the_menu_marks_the_picture_and_the_check_follows(win):
    qa = win.menu_actions["mark"]
    assert win.catalog.marked_count == 0 and not qa.isChecked()
    qa.trigger()
    assert win.catalog.marked_count == 1 and qa.isChecked() and win.buttons["mark"].isChecked()
    QTest.keyClick(win, Qt.Key_Space)  # the key un-marks; the menu follows
    assert win.catalog.marked_count == 0 and not qa.isChecked()


def test_move_on_mark_check_mirrors_the_button(win):
    qa = win.menu_actions["advance"]
    before = qa.isChecked()
    assert before == win.buttons["advance"].isChecked()
    win.buttons["advance"].click()
    assert qa.isChecked() == (not before)
    qa.trigger()
    assert qa.isChecked() == before == win.buttons["advance"].isChecked()


def test_quit_closes_the_window(win):
    win.menu_actions["quit"].trigger()
    assert not win.isVisible()


def test_quit_key_closes_the_window(win):
    win.setFocus()
    QTest.keyClick(win, Qt.Key_Q, CTRL)
    assert not win.isVisible()


def test_navigation_from_the_menu_steps(win):
    win.menu_actions["next"].trigger()
    assert win.catalog.index == 1
    win.menu_actions["back"].trigger()
    assert win.catalog.index == 0


def test_actions_are_disabled_where_the_key_does_nothing(empty_win):
    w = empty_win
    for a in act.ACTIONS:
        assert w.menu_actions[a.id].isEnabled() == (not a.needs_picture), a.id
    assert w.menu_actions["quit"].isEnabled() and w.menu_actions["help"].isEnabled()


def test_crop_story_are_disabled_in_the_review(win):
    win.open_review()
    assert not win.menu_actions["crop"].isEnabled() and win.menu_actions["rotate_left"].isEnabled()
    win.close_review()
    assert win.menu_actions["crop"].isEnabled()


# -- advanced-only actions ------------------------------------------------------------------------
ADVANCED_IDS = [a.id for a in act.ACTIONS if a.flag]


def test_advanced_actions_exist():
    assert set(ADVANCED_IDS) == {"start_fresh", "compare", "peaking", "afpoint"}


def test_advanced_actions_follow_set_advanced(win):
    assert all(not win.menu_actions[i].isVisible() for i in ADVANCED_IDS)
    win.set_advanced(True)
    assert all(win.menu_actions[i].isVisible() for i in ADVANCED_IDS)
    win.set_advanced(False)
    assert all(not win.menu_actions[i].isVisible() for i in ADVANCED_IDS)


def test_advanced_actions_start_hidden_in_classic_and_shown_when_started_advanced(tmp_path):
    features.set_mode(features.ADVANCED)
    w = make_window(tmp_path / "adv", 2)
    try:
        assert all(w.menu_actions[i].isVisible() for i in ADVANCED_IDS)
    finally:
        w.close()


# -- the `?` overlay ------------------------------------------------------------------------------
def test_question_mark_opens_and_closes_the_overlay_over_the_picture(win):
    win.setFocus()
    assert win._shortcuts is None
    QTest.keyClick(win, Qt.Key_Question, SHIFT)
    ov = win._shortcuts
    assert ov is not None and ov.isVisible() and ov.parent() is win
    assert ov.geometry() == win.rect()
    QTest.keyClick(ov, Qt.Key_Question, SHIFT)
    assert not ov.isVisible()
    QTest.keyClick(win, Qt.Key_Question, SHIFT)
    assert win._shortcuts.isVisible()
    QTest.keyClick(win._shortcuts, Qt.Key_Escape)
    assert not win._shortcuts.isVisible()


def test_overlay_lists_every_action_with_its_key_and_the_context_keys(win):
    win.menu_actions["help"].trigger()
    ov = win._shortcuts
    assert ov.isVisible()
    texts = ov.labels()
    for a in act.ACTIONS:
        if a.id in act.PILL_DIGITS:  # a digit row appears only once a pill owns it (test_pills_ui covers it)
            continue
        assert a.label in texts, a.id
        assert act.native_keys(a) in texts, a.id
    for _g, keys, what in act.CONTEXT_KEYS:
        assert keys in texts and what in texts
    for g in act.GROUPS:
        assert g.upper() in texts


def test_overlay_swallows_other_keys(monkeypatch, win):
    win.toggle_shortcuts()
    mark = watch(monkeypatch, win, "toggle_mark")
    step = watch(monkeypatch, win, "step")
    QTest.keyClick(win._shortcuts, Qt.Key_Space)
    QTest.keyClick(win._shortcuts, Qt.Key_Right)
    assert (mark.n, step.n) == (0, 0) and win._shortcuts.isVisible()


def test_overlay_click_closes_and_follows_resize(win):
    win.toggle_shortcuts()
    win.resize(1000, 700)
    QApplication.processEvents()
    assert win._shortcuts.geometry() == win.rect()
    QTest.mouseClick(win._shortcuts, Qt.MouseButton.LeftButton)
    assert not win._shortcuts.isVisible()


def test_overlay_dims_rows_whose_feature_is_off(win):
    win.toggle_shortcuts()
    dim = [lbl.text() for lbl in win._shortcuts.findChildren(QLabel) if lbl.property("role") == "dim"]
    assert "Compare" in dim and "Peaking" in dim


def test_control_keys_show_natively():
    a = act.by_id()["mark_all"]
    assert act.native_keys(a) == QKeySequence("Ctrl+A").toString(QKeySequence.SequenceFormat.NativeText)
