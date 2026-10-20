"""Design round 2, the layouts: A (the Quiet bar, default) and B (Lights out, Tab), the theme toggle, retheme.

Real QTest key events over a window on tiny JPEGs in tmp_path; settings are the per-test SPUZA_SETTINGS_FILE."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QToolButton, QVBoxLayout, QWidget

from spuza import features
from spuza.catalog import Catalog, Prefetcher, scan
from spuza.ui import settings, theme
from spuza.ui.window import KeyButton, MainWindow

APP = QApplication.instance() or QApplication([])
CTRL = Qt.KeyboardModifier.ControlModifier


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


def chrome(w):
    return (w.quiet_bar, w.sidebar, w.inspector, w.filmstrip, w.statusBar())


def tab(w):
    QTest.keyClick(w, Qt.Key_Tab)


# -- A is the default ----------------------------------------------------------------------------
def test_a_is_the_default_on_a_fresh_settings_file(win, tmp_path):
    assert win.lights_out is False
    assert all(p.isVisible() for p in chrome(win))
    assert not win.pill.isVisible() and win.menuBar().isVisible()
    assert not win.toolbar.isVisible() and not win.toolbar2.isVisible()  # the old rows are never shown
    assert win.prefs.value(settings.LIGHTS_OUT) is None  # nothing is written until the user flips it
    assert win.menu_actions["lights_out"].isChecked() is False


def test_bar_holds_the_same_key_buttons_icon_only_and_a_filled_copy(win):
    bar = win.quiet_bar
    for name in ("back", "next", "mark", "zoom", "review"):
        b = win.buttons[name]
        assert isinstance(b, KeyButton) and b.parent() is bar and b in bar.buttons()
        assert b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly and not b.icon().isNull()
        assert b.isVisible() and b.focusPolicy() == Qt.FocusPolicy.NoFocus
    copy = win.buttons["copy"]
    assert isinstance(copy, KeyButton) and copy.parent() is bar and copy.isVisible()
    assert copy.property("primary") is True and copy.objectName() == "btn_copy"
    order = [w for w in bar.findChildren(QToolButton) if w.parent() is bar]
    assert order.index(win.buttons["back"]) < order.index(win.buttons["next"]) < order.index(win.buttons["mark"]) \
        < order.index(win.buttons["zoom"]) < order.index(win.buttons["review"]) < order.index(copy)
    assert bar.title() == "card  ·  3"
    # the bar's buttons still drive the same handlers and stay in step
    win.buttons["mark"].click()
    assert win.catalog.is_marked(0) and win.buttons["mark"].isChecked()


def test_tooltips_are_one_line_name_and_key(win):
    for name, tip in (("back", "Back  ←/J"), ("next", "Next  →/K"), ("mark", "Mark  Space/M"), ("zoom", "Zoom 100%  Z"),
                      ("review", "Review  R"), ("copy", "Copy…  C")):
        assert win.buttons[name].toolTip().replace("\xa0", " ") == tip, name


def test_overflow_holds_the_menu_actions_with_their_keys(win):
    menu = win.quiet_bar.overflow_menu()
    got = [(a.text(), a.shortcut().toString()) for a in menu.actions() if not a.isSeparator() and a.isVisible()]
    assert got == [("Move on mark", "A"), ("Full screen", "F"), ("Preferences…", "Ctrl+,"), ("Lights out", "Tab")]
    assert menu.actions()[0] is win.menu_actions["advance"]  # the menu bar's own QAction: label, check and key in step
    win.set_advance_on_mark(True)
    assert win.menu_actions["advance"].isChecked()


def test_overflow_adds_the_advanced_three_only_when_their_flags_are_on(tmp_path):
    features.set_mode(features.ADVANCED)
    w = make_window(tmp_path / "card", 3)
    try:
        texts = [a.text() for a in w.quiet_bar.overflow_menu().actions() if not a.isSeparator() and a.isVisible()]
        assert texts[-3:] == ["Compare", "Peaking", "AF point"]
        w.set_advanced(False)
        texts = [a.text() for a in w.quiet_bar.overflow_menu().actions() if not a.isSeparator() and a.isVisible()]
        assert "Compare" not in texts and "Peaking" not in texts and "AF point" not in texts
    finally:
        w.close()


def test_prefs_from_the_overflow_runs_the_handler(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win, "open_prefs", lambda: calls.append(1))
    win.menu_actions["prefs"].trigger()
    assert calls == [1]


# -- B: Lights out --------------------------------------------------------------------------------
def test_tab_flips_to_b_and_back(win):
    win.setFocus()
    tab(win)
    assert win.lights_out and not any(p.isVisible() for p in chrome(win))
    assert win.menuBar().isVisible()  # the menu bar stays
    assert win.pill.isVisible() and win.viewer.isVisible()
    assert win.menu_actions["lights_out"].isChecked()
    tab(win)
    assert not win.lights_out and all(p.isVisible() for p in chrome(win)) and not win.pill.isVisible()
    assert not win.menu_actions["lights_out"].isChecked()


def test_pill_shows_mark_state_position_and_buttons(win, monkeypatch):
    win.set_lights_out(True)
    assert win.pill.position_text() == "1 / 3" and win.pill.marked_text() == ""
    QTest.keyClick(win, Qt.Key_Space)
    assert win.pill.marked_text() == "Marked"
    QTest.keyClick(win, Qt.Key_Right)
    assert win.pill.position_text() == "2 / 3" and win.pill.marked_text() == ""
    calls = []
    monkeypatch.setattr(win, "open_copy", lambda: calls.append("copy"))
    monkeypatch.setattr(win, "toggle_shortcuts", lambda: calls.append("help"))
    win.pill.copy_button().click()
    win.pill.help_button().click()
    assert calls == ["copy", "help"]
    assert win.pill.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_view_menu_lights_out_item_toggles_and_checks(win):
    qa = win.menu_actions["lights_out"]
    assert qa.isCheckable() and qa.shortcut().toString() == "Tab"
    qa.trigger()
    assert win.lights_out and qa.isChecked() and win.pill.isVisible()
    qa.trigger()
    assert not win.lights_out and not qa.isChecked()


def test_mode_is_saved_and_restored_and_fresh_start_writes_nothing(tmp_path):
    w = make_window(tmp_path / "card", 3)
    path = Path(w.prefs.fileName())
    assert not path.exists()  # starting in A wrote nothing
    tab(w)
    w.close()
    assert QSettings(str(path), QSettings.Format.IniFormat).value(settings.LIGHTS_OUT) in (True, "true")
    w2 = make_window(tmp_path / "card2", 3)
    try:
        assert w2.lights_out and w2.pill.isVisible() and not w2.quiet_bar.isVisible()
        assert w2.menu_actions["lights_out"].isChecked()
    finally:
        w2.close()


def test_a_junk_saved_mode_means_a(tmp_path):
    QSettings(os.environ["SPUZA_SETTINGS_FILE"], QSettings.Format.IniFormat).setValue(settings.LIGHTS_OUT, "banana")
    w = make_window(tmp_path / "card", 3)
    try:
        assert w.lights_out is False and w.quiet_bar.isVisible()
    finally:
        w.close()


def test_status_messages_show_over_the_photo_in_b(win):
    win.say("hello a")
    assert win.statusBar().currentMessage() == "hello a" and not win._toast.isVisible()
    win.set_lights_out(True)
    win.say("hello b")
    assert win._toast.isVisible() and win._toast.text() == "hello b"
    assert win._toast.geometry().bottom() < win.pill.geometry().top()  # above the pill, not on it
    win.set_lights_out(False)
    assert not win._toast.isVisible()


# -- full screen is independent -----------------------------------------------------------------
def test_f_then_tab_shows_only_the_photo_and_leaving_restores_the_new_mode(win):
    QTest.keyClick(win, Qt.Key_F)
    assert win.is_bleed and not win.pill.isVisible()
    tab(win)  # inside full screen the mode flips (and is saved), the screen stays the photo alone
    assert win.lights_out
    assert not win.pill.isVisible() and not any(p.isVisible() for p in chrome(win))
    if sys.platform != "darwin":
        assert not win.menuBar().isVisible()
    QTest.keyClick(win, Qt.Key_Escape)
    assert not win.is_bleed and win.lights_out
    assert win.pill.isVisible() and not win.quiet_bar.isVisible() and win.menuBar().isVisible()
    QTest.keyClick(win, Qt.Key_F)
    tab(win)  # back to A while full screen is on
    QTest.keyClick(win, Qt.Key_F)
    assert not win.lights_out and win.quiet_bar.isVisible() and not win.pill.isVisible()


def test_leaving_bleed_restores_a_chrome_when_in_a(win):
    QTest.keyClick(win, Qt.Key_F)
    assert not any(p.isVisible() for p in chrome(win)) and not win.pill.isVisible()
    QTest.keyClick(win, Qt.Key_F)
    assert all(p.isVisible() for p in chrome(win)) and not win.toolbar.isVisible()


# -- every page works in B -------------------------------------------------------------------------
def test_review_in_b_has_no_pill_and_keeps_its_keys(win):
    win.catalog.toggle_mark(0)
    win.catalog.toggle_mark(1)
    win.set_lights_out(True)
    QTest.keyClick(win, Qt.Key_R)
    assert win.stack.currentIndex() == 1 and not win.pill.isVisible() and not win.quiet_bar.isVisible()
    QTest.keyClick(win, Qt.Key_Right)
    assert win.review.selected_index() == 1
    QTest.keyClick(win, Qt.Key_Escape)
    assert win.stack.currentIndex() == 0 and win.pill.isVisible()


def test_crop_edit_in_b_hides_the_pill_and_keeps_the_hint_visible(win):
    win.set_lights_out(True)
    pump(lambda: win.viewer.has_image)
    QTest.keyClick(win, Qt.Key_X)
    assert win._edit == "crop" and not win.pill.isVisible()
    assert win._toast.isVisible() and win._toast.text().startswith("Crop")
    QTest.keyClick(win, Qt.Key_Escape)
    assert win._edit is None and win.pill.isVisible()
    QTest.keyClick(win, Qt.Key_S)
    assert win._edit == "story" and not win.pill.isVisible()
    QTest.keyClick(win, Qt.Key_Escape)
    assert win.pill.isVisible()


def test_compare_in_b_has_no_pill(tmp_path):
    features.set_mode(features.ADVANCED)
    w = make_window(tmp_path / "card", 3)
    try:
        w.set_lights_out(True)
        QTest.keyClick(w, Qt.Key_N)
        assert w._compare_shown() and not w.pill.isVisible()
        QTest.keyClick(w, Qt.Key_Escape)
        assert not w._compare_shown() and w.pill.isVisible()
    finally:
        w.close()


# -- Tab where it must stay Tab -------------------------------------------------------------------
def test_tab_in_a_dialog_line_edit_moves_focus_not_the_mode(win):
    dlg = QDialog(win)
    lay = QVBoxLayout(dlg)
    first, second = QLineEdit(), QLineEdit()
    lay.addWidget(first)
    lay.addWidget(second)
    dlg.show()
    dlg.activateWindow()
    pump(lambda: APP.activeWindow() is dlg)
    first.setFocus()
    pump(lambda: APP.focusWidget() is first)
    QTest.keyClick(first, Qt.Key_Tab)
    assert APP.focusWidget() is second
    assert win.lights_out is False and all(p.isVisible() for p in chrome(win))
    dlg.close()


def test_tab_in_the_prefs_sheet_moves_focus_not_the_mode(win):
    win.open_prefs()
    sheet = win._prefs_sheet
    assert sheet is not None and sheet.isVisible()
    sheet.activateWindow()
    pump(lambda: APP.activeWindow() is sheet)
    tabbable = [w for w in sheet.findChildren(QWidget) if w.isVisible() and w.focusPolicy() & Qt.FocusPolicy.TabFocus]
    assert len(tabbable) >= 2
    tabbable[0].setFocus()
    pump(lambda: APP.focusWidget() is tabbable[0])
    QTest.keyClick(tabbable[0], Qt.Key_Tab)
    assert win.lights_out is False
    assert APP.focusWidget() is not tabbable[0] and sheet.isAncestorOf(APP.focusWidget())
    sheet.reject()


def test_tab_while_the_shortcut_overlay_is_open_is_swallowed(win):
    QTest.keyClick(win, Qt.Key_Question, Qt.KeyboardModifier.ShiftModifier)
    assert win._overlay_open()
    tab(win)
    assert win.lights_out is False
    QTest.keyClick(win._shortcuts, Qt.Key_Escape)  # the overlay holds the focus
    assert not win._overlay_open()
    tab(win)
    assert win.lights_out


def test_tab_and_ctrl_t_shortcuts_do_not_auto_repeat(win):
    from PySide6.QtGui import QShortcut

    for key in ("Tab", "Ctrl+T"):  # QTest cannot make autorepeat events; a held key must not flip the layout back and forth
        found = [s for s in win.findChildren(QShortcut) if s.key().toString() == key]
        assert len(found) == 1 and found[0].autoRepeat() is False, key


# -- theme ------------------------------------------------------------------------------------------
def test_ctrl_t_flips_the_theme_saves_it_and_the_menu_check_follows(win):
    qa = win.menu_actions["dark_theme"]
    theme.apply(APP, True)
    win.refresh_actions()
    assert qa.isCheckable() and qa.shortcut().toString() == "Ctrl+T" and qa.isChecked()
    QTest.keyClick(win, Qt.Key_T, CTRL)
    assert not theme.current().dark and not qa.isChecked() and theme.choice(win.prefs) == "light"
    assert APP.palette().color(QPalette.Window).name() == theme.LIGHT.window
    qa.trigger()
    assert theme.current().dark and qa.isChecked() and theme.choice(win.prefs) == "dark"


def test_a_plain_t_does_not_touch_the_theme(win):
    QTest.keyClick(win, Qt.Key_T)
    assert theme.choice(win.prefs) is None and theme.current().dark


def test_the_check_follows_an_os_follow(win):
    theme.apply(APP, True)
    win.refresh_actions()
    assert win.menu_actions["dark_theme"].isChecked()
    theme.apply(APP, False)  # what theme.follow does on a macOS scheme flip, then its on_change
    win.retheme()
    assert not win.menu_actions["dark_theme"].isChecked()


def _pixel_colors(btn):
    img = btn.icon().pixmap(18, 18).toImage()
    return {img.pixelColor(x, y).name() for x in range(img.width()) for y in range(img.height())
            if img.pixelColor(x, y).alpha() > 200}


def test_retheme_restyles_bar_icons_pill_and_compare(tmp_path):
    features.set_mode(features.ADVANCED)
    w = make_window(tmp_path / "card", 3)
    try:
        w.set_lights_out(False)
        theme.apply(APP, True)
        w.retheme()
        dark_icon = _pixel_colors(w.buttons["back"])
        dark_hud = w.pill.hud_color().name(QColor.NameFormat.HexArgb)
        QTest.keyClick(w, Qt.Key_N)
        assert w.compare is not None
        calls = []
        orig = w.compare.retheme
        w.compare.retheme = lambda: (calls.append(1), orig())
        QTest.keyClick(w, Qt.Key_T, CTRL)
        assert calls == [1]
        assert _pixel_colors(w.buttons["back"]) != dark_icon
        assert theme.LIGHT.text in {c.lower() for c in _pixel_colors(w.buttons["back"])}
        assert w.quiet_bar.styleSheet().count(theme.LIGHT.window) >= 1
        assert w._compare_strip.styleSheet().count(theme.LIGHT.hud) == 1
        assert dark_hud == theme.qcolor(theme.DARK.hud).name(QColor.NameFormat.HexArgb)
        assert w.pill.hud_color().name(QColor.NameFormat.HexArgb) == theme.qcolor(theme.LIGHT.hud).name(
            QColor.NameFormat.HexArgb)
    finally:
        w.close()


def test_toggling_the_theme_and_the_mode_on_an_empty_folder_is_clean(empty_win):
    w = empty_win
    QTest.keyClick(w, Qt.Key_T, CTRL)
    tab(w)
    QTest.keyClick(w, Qt.Key_T, CTRL)
    tab(w)
    assert w.pill.position_text() == "0 / 0" or not w.pill.isVisible()
    assert w.menu_actions["dark_theme"].isEnabled() and w.menu_actions["lights_out"].isEnabled()


def test_theme_and_mode_persist_across_a_fresh_window(tmp_path):
    w = make_window(tmp_path / "card", 3)
    QTest.keyClick(w, Qt.Key_T, CTRL)
    tab(w)
    w.close()
    theme._current = theme.DARK  # a new process: the theme module starts dark, cli.main then calls theme.start
    w2 = make_window(tmp_path / "card2", 3)
    try:
        theme.start(APP, w2.prefs, w2.retheme)
        assert not theme.current().dark and w2.lights_out
        assert not w2.menu_actions["dark_theme"].isChecked() and w2.menu_actions["lights_out"].isChecked()
        assert w2.pill.isVisible()
    finally:
        w2.close()
