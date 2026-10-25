"""Design round 2, the action policy (design-system.md, "action policy"): nothing is reachable only by mouse, and
nothing is reachable only by a hidden key.

* every action in ui/actions.py has a key, and every key the window handles is an action's or a context row's;
* every visible button and every menu item names its key;
* Tab flips A <-> B (window focus, review grid focus), and every action still works by its key in B;
* the mode and the theme survive a restart; the pill exists only in B.
"""

from __future__ import annotations

import ast
import importlib.util
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QKeySequence, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QToolButton

from spuza import features
from spuza.catalog import Catalog, Prefetcher, scan
from spuza.ui import actions as act
from spuza.ui import theme
from spuza.ui.window import MainWindow

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_readme_for_policy", ROOT / "docs" / "check_readme.py")
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


@pytest.fixture(autouse=True)
def isolated_pills(tmp_path, monkeypatch):
    """The window loads and saves pills in app data: never the real one."""
    monkeypatch.setenv("SPUZA_PILLS_DIR", str(tmp_path / "pills"))


@pytest.fixture(autouse=True)
def clean_theme():
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


def make_window(folder: Path, n: int = 3) -> MainWindow:
    folder.mkdir(exist_ok=True)
    for i in range(n):
        Image.new("RGB", (64, 48), (40 * i + 30, 90, 160)).save(folder / f"P{i}.jpg")
    cat = Catalog(scan(folder))
    w = MainWindow(cat, Prefetcher(cat), folder)
    w.resize(1200, 800)
    w.show()
    pump(lambda: w.viewer.has_image)
    return w


@pytest.fixture
def win(tmp_path):
    w = make_window(tmp_path / "card")
    yield w
    w.close()


@pytest.fixture
def adv(tmp_path):
    features.set_mode(features.ADVANCED)
    w = make_window(tmp_path / "card")
    yield w
    w.close()


# -- 1. the table and the code agree -----------------------------------------------------------------
KEYLESS = {"start_fresh", "about"}  # menu only: no key (Start fresh lost its U in 0.16.0; About never had one)


def test_every_action_has_a_key_and_a_label():
    for a in act.ACTIONS:
        assert (a.keys and all(k.strip() for k in a.keys)) or a.id in KEYLESS, a.id
        assert all(not QKeySequence(k).isEmpty() for k in a.keys), f"{a.id}: {a.keys} is not a key Qt can parse"
        assert a.label.strip(), a.id


def test_every_key_the_window_handles_is_an_action_or_a_context_row():
    tree = ast.parse((ROOT / "src" / "spuza" / "ui" / "window.py").read_text(encoding="utf-8"))
    code = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == "MainWindow":
            for f in n.body:
                if isinstance(f, ast.FunctionDef) and f.name == "keyPressEvent":
                    code |= cr._key_attrs(f)
    code |= cr._sequences(tree)
    table = {cr._seq_key(k) for a in act.ACTIONS for k in a.keys}
    context = set()
    for _g, keys, _what in act.CONTEXT_KEYS:
        context |= {cr._readme_key(t) for t in keys.replace(" - ", " ").split()}
    assert code - table - context == set(), code - table - context
    assert {"tab", "ctrl+t"} <= table  # the two new keys are in the table, so the menu, overlay and README see them


# -- 2. every visible control names its key -----------------------------------------------------------
KEYLESS_OPENERS = {"quietMore"}  # the bar's ellipsis only opens the menu below it; each item there names its key


def _key_of_tooltip(tip: str) -> str:
    tip = tip.replace("\xa0", " ")
    return tip.split("  ", 1)[1].strip() if "  " in tip else ""


def test_every_visible_bar_button_names_its_key(win):
    shown = [b for b in win.quiet_bar.findChildren(QToolButton) if b.isVisible()]
    assert {b.objectName() for b in shown} >= {"btn_back", "btn_next", "btn_mark", "btn_zoom", "btn_review",
                                               "btn_copy", "quietMore"}
    for b in shown:
        if b.objectName() in KEYLESS_OPENERS:
            assert b.menu() is win.quiet_bar.overflow_menu()
            continue
        assert "\n" not in b.toolTip()
        key = _key_of_tooltip(b.toolTip())
        assert key and key == b.cap, (b.objectName(), b.toolTip())


def test_overflow_items_and_every_menu_item_name_a_key(adv):
    for qa in adv.quiet_bar.overflow_menu().actions():
        if qa.isSeparator() or not qa.isVisible():
            continue
        assert not qa.shortcut().isEmpty(), qa.text()
    for top in adv.menuBar().actions():
        for qa in top.menu().actions():
            if qa.isSeparator() or not qa.isVisible():
                continue
            assert not qa.shortcut().isEmpty() or qa.text() in {"Start fresh", "About Spuza"}, (top.text(), qa.text())
    shown = {qa.text() for qa in adv.quiet_bar.overflow_menu().actions() if qa.isVisible() and not qa.isSeparator()}
    assert shown == {"Move on mark", "Full screen", "Preferences…", "Lights out", "Compare", "Peaking", "AF point"}


def test_pill_buttons_name_their_keys(win):
    assert _key_of_tooltip(win.pill.copy_button().toolTip()) == "C"
    assert _key_of_tooltip(win.pill.help_button().toolTip()) == "?"


# -- 3. Tab ------------------------------------------------------------------------------------------
def test_tab_toggles_b_and_back_with_focus_on_the_window(win):
    win.setFocus()
    assert not win.lights_out
    QTest.keyClick(win, Qt.Key_Tab)
    assert win.lights_out
    QTest.keyClick(win, Qt.Key_Tab)
    assert not win.lights_out


def test_tab_toggles_b_and_back_with_the_review_grid_focused(win):
    win.catalog.toggle_mark(0)
    win.catalog.toggle_mark(1)
    win.open_review()
    pump(lambda: win.stack.currentIndex() == 1)
    grid = win.review.view
    grid.setFocus()
    assert APP.focusWidget() is grid or win.review.isAncestorOf(APP.focusWidget())
    QTest.keyClick(grid, Qt.Key_Tab)
    assert win.lights_out and win.stack.currentIndex() == 1
    QTest.keyClick(grid, Qt.Key_Tab)
    assert not win.lights_out and win.stack.currentIndex() == 1


# -- 4. every action works by its key, in A and in B --------------------------------------------------
# action id -> (owner attribute path, method) that its key must call exactly once. A new action must be added here.
PROBES = {
    "copy": ("", "open_copy"), "prefs": ("", "open_prefs"), "quit": ("", "close"),
    "mark": ("", "toggle_mark"), "advance": ("", "set_advance_on_mark"), "mark_all": ("", "mark_all"),
    "unmark_all": ("", "mark_all"), "undo_mark_all": ("", "undo_mark_all"), "start_fresh": ("", "start_fresh"),
    "back": ("", "step"), "next": ("", "step"), "zoom": ("viewer", "toggle_zoom"), "zoom_in": ("viewer", "zoom_in"),
    "zoom_out": ("viewer", "zoom_out"), "fullscreen": ("", "set_bleed"), "review": ("", "toggle_review"),
    "compare": ("", "open_compare"), "peaking": ("", "toggle_peaking"), "afpoint": ("", "toggle_marker"),
    "lights_out": ("", "set_lights_out"), "dark_theme": ("", "act_dark_theme"),
    "rotate_left": ("", "rotate"), "rotate_right": ("", "rotate"), "crop": ("", "start_crop"),
    "story": ("", "start_story"), "remove": ("", "remove_derived"), "help": ("", "toggle_shortcuts"), "about": ("", "act_about"),
    # pills (0.12): the digits call _pill_key(n); Ctrl+Shift+P is a window shortcut
    **{f"pill_{n}": ("", "_pill_key") for n in range(1, 10)}, "pill_clear": ("", "_pill_key"),
    "pills_manage": ("", "open_pill_manager"),
    # stars (0.14): Ctrl+1..5 set, Ctrl+0 clears; each is a window shortcut calling _stars_key(n)
    **{f"stars_{n}": ("", "_stars_key") for n in range(1, 6)}, "stars_clear": ("", "_stars_key"),
}


def test_every_action_has_a_probe():
    assert set(PROBES) == {a.id for a in act.ACTIONS}


class Count:
    def __init__(self):
        self.n = 0

    def __call__(self, *a, **k):
        self.n += 1


def _press(win, seq: str) -> None:
    kc = QKeySequence(seq)[0]
    QTest.keyClick(win, kc.key(), kc.keyboardModifiers())


@pytest.mark.parametrize("lights_out", [False, True], ids=["A", "B"])
@pytest.mark.parametrize("a", act.ACTIONS, ids=lambda a: a.id)
def test_every_key_of_every_action_calls_its_handler_once(monkeypatch, adv, a, lights_out):
    adv.set_lights_out(lights_out)
    adv.setFocus()
    path, method = PROBES[a.id]
    owner = getattr(adv, path) if path else adv
    for key in a.keys:
        counter = Count()
        monkeypatch.setattr(owner, method, counter)
        _press(adv, key)
        assert counter.n == 1, (a.id, key, "B" if lights_out else "A", counter.n)
        monkeypatch.undo()
        adv.set_lights_out(lights_out)  # a key that is not patched through (none now) could have moved the mode


@pytest.mark.parametrize("lights_out", [False, True], ids=["A", "B"])
def test_review_keys_work_in_b_with_the_grid_focused(adv, lights_out):
    adv.catalog.toggle_mark(0)
    adv.catalog.toggle_mark(1)
    adv.set_lights_out(lights_out)
    adv.open_review()
    pump(lambda: adv.stack.currentIndex() == 1)
    adv.review.leave_zoom()  # review now opens on the zoom page (448/449); this test drives the grid's list view
    QTest.keyClick(adv.review.view, Qt.Key_Right)
    sel = adv.review.selected_index()
    QTest.keyClick(adv.review.view, Qt.Key_Right)
    assert adv.review.selected_index() in (sel, 1)
    QTest.keyClick(adv.review.view, Qt.Key_Escape)
    assert adv.stack.currentIndex() == 0


# -- 5. persistence and the pill -----------------------------------------------------------------------
def test_mode_and_theme_persist_across_a_fresh_window_on_the_same_settings_file(tmp_path):
    w = make_window(tmp_path / "one")
    QTest.keyClick(w, Qt.Key_Tab)
    QTest.keyClick(w, Qt.Key_T, Qt.KeyboardModifier.ControlModifier)
    assert w.lights_out and not theme.current().dark
    w.close()
    theme._current = theme.DARK  # a new process
    w2 = make_window(tmp_path / "two")
    try:
        theme.start(APP, w2.prefs, w2.retheme)  # what cli.main does with the window's own prefs
        assert w2.lights_out and not theme.current().dark
        assert w2.menu_actions["lights_out"].isChecked() and not w2.menu_actions["dark_theme"].isChecked()
    finally:
        w2.close()


def test_the_pill_is_visible_only_in_b_and_not_in_full_screen(win):
    assert not win.pill.isVisible()  # A
    win.setFocus()
    QTest.keyClick(win, Qt.Key_Tab)
    assert win.pill.isVisible()  # B
    QTest.keyClick(win, Qt.Key_F)
    assert win.is_bleed and not win.pill.isVisible()  # B + full screen: the photo alone
    QTest.keyClick(win, Qt.Key_Tab)
    assert not win.pill.isVisible()  # A + full screen
    QTest.keyClick(win, Qt.Key_F)
    assert not win.pill.isVisible()  # A
    QTest.keyClick(win, Qt.Key_Tab)
    assert win.pill.isVisible()


# -- 2b. every visible button of every view names its key (bug-442) -----------------------------------------
# Genuinely keyless controls, each with its reason. Anything else a view shows must read "<label>  <key>".
KEYLESS_BUTTONS = {
    "quietMore": "opens the overflow menu; each item in it names its key",
    "Choose folder…": "Copy sheet: opens a folder dialog; no key exists for it",
    "Reset to defaults": "Preferences sheet: a mouse-only reset on purpose, so it cannot be hit by habit",
    "Stay": "Copy sheet: cancels the pending quit-after-copy; no key exists for it",
    "Show": "Copy sheet: reveals the result in the file manager; no key exists for it",
}
FORM_CONTROLS = ("QCheckBox",)  # settings toggled with Space when focused: not actions, so no key chip


def nameless_buttons(root) -> list[str]:
    """Every visible button under `root` that does not read '<label>  <key>' (minus the commented allow-list)."""
    bad = []
    for b in root.findChildren(QAbstractButton):
        if not b.isVisible() or type(b).__name__ in FORM_CONTROLS:
            continue
        label = b.objectName() or b.text()
        if label in KEYLESS_BUTTONS or any(b.text().startswith(k) for k in KEYLESS_BUTTONS if k.isalpha()):
            continue
        if not _key_of_tooltip(b.toolTip()):
            bad.append(f"{type(b).__name__} {label!r} tooltip={b.toolTip()!r}")
        elif hasattr(b, "cap"):
            assert _key_of_tooltip(b.toolTip()) == b.cap, (label, b.toolTip(), b.cap)
    return bad


@pytest.fixture
def two(tmp_path):
    features.set_mode(features.ADVANCED)
    w = make_window(tmp_path / "card", n=4)
    w.catalog.toggle_mark(0)
    w.catalog.toggle_mark(1)
    yield w
    w.close()


def test_single_view_buttons_name_their_keys(two):
    assert nameless_buttons(two) == []


def test_review_grid_and_zoom_page_buttons_name_their_keys(two):
    two.open_review()
    pump(lambda: two.stack.currentIndex() == 1)
    shown = {b.text() for b in two.review.findChildren(QAbstractButton) if b.isVisible()}
    assert {"Zoom", "Unmark"} <= shown
    assert nameless_buttons(two) == []
    two.review.show_zoom()
    QTest.qWait(20)
    assert nameless_buttons(two) == []


def test_compare_buttons_name_their_keys(two):
    two.open_compare()
    pump(lambda: two._compare_shown())
    assert nameless_buttons(two) == []


@pytest.mark.parametrize("start", ["start_crop", "start_story"])
def test_crop_and_story_edit_buttons_name_their_keys(two, start):
    getattr(two, start)()
    assert two._edit is not None
    assert nameless_buttons(two) == []


def test_lights_out_buttons_name_their_keys(two):
    two.set_lights_out(True)
    pump(lambda: two.pill.isVisible())
    assert {b.objectName() for b in two.pill.findChildren(QAbstractButton)} >= {"pillHelp", "pillCopy"}
    assert nameless_buttons(two) == []


def test_dialog_buttons_name_their_keys(two, tmp_path):
    from spuza.ui.copy_sheet import CopySheet
    from spuza.ui.prefs_sheet import PrefsSheet

    prefs = PrefsSheet(12, 8)
    prefs.show()
    copy = CopySheet(two.catalog.marked(), tmp_path / "out")
    copy.show()
    try:
        assert nameless_buttons(prefs) == []
        assert nameless_buttons(copy) == []
    finally:
        prefs.close()
        copy.close()


# -- 2c. J is back and K is next, as they sit beside the arrows --------------------------------------------
def test_j_goes_back_and_k_goes_forward_in_the_single_view(two):
    QTest.keyClick(two, Qt.Key_K)
    assert two.catalog.index == 1
    QTest.keyClick(two, Qt.Key_K)
    assert two.catalog.index == 2
    QTest.keyClick(two, Qt.Key_J)
    assert two.catalog.index == 1
    assert act.by_id()["back"].keys == ("Left", "J") and act.by_id()["next"].keys == ("Right", "K")
    assert two.buttons["back"].cap == "←/J" and two.buttons["next"].cap == "→/K"


def test_j_goes_back_and_k_goes_forward_in_compare(two):
    two.open_compare()
    pump(lambda: two._compare_shown())
    start = two.compare.focused_index()
    QTest.keyClick(two.compare, Qt.Key_K)
    assert two.compare.focused_index() == start + 1
    QTest.keyClick(two.compare, Qt.Key_J)
    assert two.compare.focused_index() == start


# -- 2d. the `?` overlay is opaque where it matters (improvement-436) -------------------------------------
@pytest.mark.parametrize("dark", [True, False])
def test_shortcut_overlay_panel_is_solid_and_the_scrim_hides_the_window(two, dark):
    theme.apply(APP, dark)
    two.retheme()
    two.sidebar.root_label.setText("SIDEBAR TEXT " * 8)  # high-contrast text that must not show through
    QTest.qWait(30)
    before = two.grab().toImage()
    two.toggle_shortcuts()
    QTest.qWait(30)
    ov = two._shortcuts
    after = two.grab().toImage()
    c = theme.current()
    # (a) inside the panel, away from every label: exactly the panel token
    pt = ov.panel.mapTo(two, ov.panel.rect().topLeft()) + QPoint(14, 14)
    assert after.pixelColor(pt).name() == QColor(c.panel).name()
    # (b) in the scrim (the window corner, outside the panel): at least 85% of the scrim colour
    for x, y in ((6, 6), (two.width() - 8, two.height() - 8), (6, two.height() - 8), (two.width() - 8, 6)):
        was, now = before.pixelColor(x, y), after.pixelColor(x, y)
        for ch in ("red", "green", "blue"):
            assert getattr(now, ch)() <= round(0.15 * getattr(was, ch)()) + 1, (x, y, ch, was.name(), now.name())
    two.toggle_shortcuts()
