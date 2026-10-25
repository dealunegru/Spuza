"""Pills in the window (Spuza 0.12.0, design A "digit pills"): keys, tray, dots, manager, menu, overlay, read-back.

Offscreen QApplication, real MainWindow on tiny synthetic JPEGs in tmp_path (< 1 MB). Every test points
SPUZA_PILLS_DIR at its own tmp dir, so nothing reaches the real app data.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication

from spuza import features
from spuza import pills as pills_mod
from spuza import xmp
from spuza.catalog import Catalog, Prefetcher, scan
from spuza.ui import actions as act
from spuza.ui import theme
from spuza.ui.window import MainWindow

APP = QApplication.instance() or QApplication([])
NAMES9 = ["Backdrop", "Delivers", "Edit it!", "Group it", "Instagrm", "Later on", "Backup 1", "Wedding1", "Portrait"]
COLORS9 = ["Blue", "Green", "Orange", "Purple", "Red", "Yellow", "Gray", "None", "Blue"]


@pytest.fixture(autouse=True)
def no_advance(monkeypatch):
    """These tests check what a pill or star key stores on the picture in view; since 0.16.0 the key then shows the
    next picture. That advance is tested in test_friend_r9_keys.py, so here it is switched off."""
    monkeypatch.setattr(MainWindow, "_advance_after_mark", lambda self: None)


@pytest.fixture(autouse=True)
def isolated_pills(tmp_path, monkeypatch):
    """Own pills dir per test; own theme state (the theme is module state)."""
    monkeypatch.setenv("SPUZA_PILLS_DIR", str(tmp_path / "pills"))
    theme._stop_following()
    yield
    theme._stop_following()
    theme._current = theme.DARK
    APP.setStyleSheet("")
    APP.setPalette(QPalette())


def settle() -> None:
    """Let the layout catch up (the tray's width follows its pills one event-loop turn later)."""
    for _ in range(3):
        QApplication.processEvents()


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond(), "condition not reached in time"


def make_pictures(folder: Path, n: int = 3, tags: dict | None = None) -> None:
    """n tiny JPEGs P0.jpg..; `tags` maps an index to the PillTags embedded in that file."""
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        path = folder / f"P{i}.jpg"
        Image.new("RGB", (64, 48), (40 * (i % 5) + 30, 90, 160)).save(path)
        if tags and i in tags:
            new, why = xmp.embed(path.read_bytes(), ".jpg", tags[i])
            assert new is not None, why
            path.write_bytes(new)


def make_window(folder: Path, n: int = 3, tags=None, size=(1200, 800), make=True) -> MainWindow:
    if make:
        make_pictures(folder, n, tags)
    cat = Catalog(scan(folder))
    w = MainWindow(cat, Prefetcher(cat), folder)
    w.resize(*size)
    w.show()
    pump(lambda: w.viewer.has_image)
    return w


def add_pills(w: MainWindow, names=("Hero", "Delivered", "Edit")) -> list:
    got = []
    for n, c in zip(names, ("Blue", "Green", "Orange", "Purple", "Red", "Yellow", "Gray", "None", "Blue")):
        got.append(w.pill_book.create(n, c))
    w._pills_edited()
    settle()
    return got


@pytest.fixture
def win(tmp_path):
    w = make_window(tmp_path / "card")
    yield w
    w.close()


@pytest.fixture
def hero(win):
    add_pills(win)
    return win


def names_of(w: MainWindow, i: int = 0) -> tuple[str, ...]:
    return w.pill_index.get(w.catalog.pictures[i])


# -- keys -------------------------------------------------------------------------------------------------
def test_digit_toggles_a_pill_on_the_current_picture(hero):
    w = hero
    w.setFocus()
    QTest.keyClick(w, Qt.Key_1)
    assert names_of(w) == ("Hero",)
    QTest.keyClick(w, Qt.Key_2)
    assert set(names_of(w)) == {"Hero", "Delivered"}
    QTest.keyClick(w, Qt.Key_1)
    assert names_of(w) == ("Delivered",)
    assert w.last_message == "Hero off"
    assert names_of(w, 1) == ()  # only this picture


def test_zero_clears_every_pill_on_the_picture(hero):
    w = hero
    QTest.keyClick(w, Qt.Key_1)
    QTest.keyClick(w, Qt.Key_3)
    QTest.keyClick(w, Qt.Key_0)
    assert names_of(w) == ()
    assert "cleared" in w.last_message
    QTest.keyClick(w, Qt.Key_0)
    assert w.last_message == "No pills to clear"


def test_a_digit_with_no_pill_says_how_to_make_one(hero):
    QTest.keyClick(hero, Qt.Key_4)
    manage = act.native_keys(act.by_id()["pills_manage"])  # "Ctrl+Shift+P" here, the command-key glyphs on a Mac
    assert hero.last_message == f"No pill on 4 — {manage} to make one"
    assert names_of(hero) == ()


def test_a_held_digit_does_not_flip_the_pill(hero):
    from PySide6.QtGui import QKeyEvent

    ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_1, Qt.KeyboardModifier.NoModifier, "1", True, 1)
    hero.keyPressEvent(ev)
    assert names_of(hero) == ()


def test_pills_follow_the_picture_in_view(hero):
    w = hero
    QTest.keyClick(w, Qt.Key_1)
    QTest.keyClick(w, Qt.Key_Right)
    assert w.catalog.index == 1 and w._current_pill_names() == ()
    QTest.keyClick(w, Qt.Key_2)
    QTest.keyClick(w, Qt.Key_Left)
    assert w._current_pill_names() == ("Hero",)


def test_digits_inside_crop_keep_their_crop_meaning(hero):
    w = hero
    w.start_crop()
    assert w._edit == "crop"
    QTest.keyClick(w, Qt.Key_3)
    assert w._crop_ratio.entry == 3 and w._edit == "crop"
    QTest.keyClick(w, Qt.Key_9)
    QTest.keyClick(w, Qt.Key_0)
    assert w._edit == "crop" and names_of(w) == ()
    assert w.pill_book.by_digit(3).name == "Edit"  # a pill owns 3, and the crop still got the ratio


def test_digits_inside_story_toggle_no_pill(hero):
    w = hero
    w.start_story()
    assert w._edit == "story"
    for k in (Qt.Key_1, Qt.Key_2, Qt.Key_9, Qt.Key_0):
        QTest.keyClick(w, k)
    assert w._edit == "story" and names_of(w) == ()


def test_digits_work_again_after_the_editor_closes(hero):
    w = hero
    w.start_crop()
    QTest.keyClick(w, Qt.Key_Escape)
    assert w._edit is None
    QTest.keyClick(w, Qt.Key_1)
    assert names_of(w) == ("Hero",)


def test_lights_out_answers_a_digit_with_a_toast(hero):
    w = hero
    w.set_lights_out(True)
    pump(lambda: w.pill.isVisible())
    QTest.keyClick(w, Qt.Key_1)
    assert w._toast.isVisible() and w._toast.text() == "Hero on"
    QTest.keyClick(w, Qt.Key_1)
    assert w._toast.text() == "Hero off"
    QTest.keyClick(w, Qt.Key_5)
    assert w._toast.text().startswith("No pill on 5")


def test_lights_out_pill_carries_the_dots_of_the_picture(hero):
    w = hero
    w.set_lights_out(True)
    QTest.keyClick(w, Qt.Key_1)
    QTest.keyClick(w, Qt.Key_2)
    assert w.pill.pill_names() == ["Hero", "Delivered"]
    assert w.pill._dots.isVisibleTo(w.pill) and w.pill._dots.colors == ["Blue", "Green"]
    QTest.keyClick(w, Qt.Key_0)
    assert w.pill.pill_names() == [] and not w.pill._dots.isVisibleTo(w.pill)


def test_ctrl_shift_p_opens_the_manager(hero):
    w = hero
    QTest.keyClick(w, Qt.Key_P, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert w._pill_mgr is not None and w._pill_mgr.isVisible()
    w._pill_mgr.reject()


# -- review ---------------------------------------------------------------------------------------------
@pytest.fixture
def grid(tmp_path):
    features.set_mode(features.ADVANCED)  # several selected pictures (Ctrl-click) need compare_view
    w = make_window(tmp_path / "card", n=4)
    add_pills(w)
    for i in (0, 1, 2):
        w.catalog.toggle_mark(i)
    w.open_review()
    w.review.leave_zoom()  # review opens on the zoom page now; these tests drive the grid
    pump(lambda: w.stack.currentIndex() == 1)
    yield w
    w.close()


def test_digit_in_the_review_grid_acts_on_the_selected_picture(grid):
    w = grid
    w.review.view.setCurrentIndex(w.review.model.index(1, 0))
    QTest.keyClick(w.review.view, Qt.Key_1)
    assert names_of(w, 1) == ("Hero",) and names_of(w, 0) == () and names_of(w, 2) == ()
    assert w.last_message == "Hero on"


def test_digit_in_the_review_grid_acts_on_every_selected_picture(grid):
    from PySide6.QtCore import QItemSelectionModel

    w = grid
    sel = w.review.view.selectionModel()
    sel.clearSelection()
    for r in (0, 2):
        sel.select(w.review.model.index(r, 0), QItemSelectionModel.SelectionFlag.Select)
    sel.setCurrentIndex(w.review.model.index(0, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
    assert w.review.selected_indices() == [0, 2]
    QTest.keyClick(w.review.view, Qt.Key_2)
    assert names_of(w, 0) == ("Delivered",) and names_of(w, 2) == ("Delivered",) and names_of(w, 1) == ()
    assert w.last_message == "Delivered on: 2 pictures"
    QTest.keyClick(w.review.view, Qt.Key_2)  # all of them have it: it comes off all
    assert names_of(w, 0) == () and names_of(w, 2) == ()
    QTest.keyClick(w.review.view, Qt.Key_1)
    QTest.keyClick(w.review.view, Qt.Key_0)
    assert names_of(w, 0) == () and names_of(w, 2) == ()


def test_digit_with_the_window_focused_in_review_also_acts_on_the_selection(grid):
    w = grid
    w.review.view.setCurrentIndex(w.review.model.index(2, 0))
    w.setFocus()
    QTest.keyClick(w, Qt.Key_1)
    assert names_of(w, 2) == ("Hero",)


def test_review_cell_draws_the_dots_bottom_right_and_not_top_right(grid):
    w = grid
    w.review.view.setCurrentIndex(w.review.model.index(1, 0))
    QTest.keyClick(w.review.view, Qt.Key_1)  # Hero = Blue on picture 1
    QTest.qWait(30)
    assert w.review.pill_colors(1) == ["Blue"]
    img = w.review.view.viewport().grab().toImage()
    cell = w.review.view.visualRect(w.review.model.index(1, 0)).adjusted(4, 4, -4, -4)
    blue = QColor(pills_mod.swatch("Blue").dark).rgb()
    bottom = [(x, y) for x in range(cell.right() - 40, cell.right() + 1) for y in range(cell.bottom() - 40, cell.bottom() + 1)
              if img.pixelColor(x, y).rgb() == blue]
    top = [(x, y) for x in range(cell.right() - 40, cell.right() + 1) for y in range(cell.y(), cell.y() + 40)
           if img.pixelColor(x, y).rgb() == blue]
    assert bottom and not top


# -- the tray ---------------------------------------------------------------------------------------------
def test_tray_lights_the_pills_of_the_current_picture(hero):
    w = hero
    QTest.keyClick(w, Qt.Key_1)
    QTest.keyClick(w, Qt.Key_3)
    caps = {c.label: c for c in w.pill_tray.capsules()}
    assert [c.label for c in w.pill_tray.capsules()] == ["Hero", "Delivered", "Edit"]  # the pill "Edit", and nothing after it
    pill_caps = [c for c in w.pill_tray.capsules() if c.kind == "pill"]
    assert [(c.label, c.key, c.lit) for c in pill_caps] == [("Hero", "1", True), ("Delivered", "2", False), ("Edit", "3", True)]
    assert caps["Hero"].tip == "Remove Hero  1"
    QTest.keyClick(w, Qt.Key_Right)
    assert not any(c.lit for c in w.pill_tray.capsules())


def test_clicking_a_capsule_toggles_the_pill(hero):
    w = hero
    cap = next(c for c in w.pill_tray.capsules() if c.label == "Delivered")
    QTest.mouseClick(w.pill_tray, Qt.MouseButton.LeftButton, pos=cap.rect.center())
    assert names_of(w) == ("Delivered",)
    cap = next(c for c in w.pill_tray.capsules() if c.label == "Delivered")
    assert cap.lit
    QTest.mouseClick(w.pill_tray, Qt.MouseButton.LeftButton, pos=cap.rect.center())
    assert names_of(w) == ()


def test_the_tray_has_no_edit_capsule_and_a_click_past_the_pills_does_nothing(hero):
    w = hero
    caps = w.pill_tray.capsules()
    assert all(c.kind == "pill" for c in caps) and len(caps) == 3
    seen = []
    w.pill_tray.manage.connect(lambda: seen.append(1))
    QTest.mouseClick(w.pill_tray, Qt.MouseButton.LeftButton, pos=QPoint(caps[-1].rect.right() + 6, 12))
    assert seen == [] and w._pill_mgr is None


def test_the_manager_still_opens_from_ctrl_shift_p_and_the_pills_menu(hero):
    w = hero
    assert hero.menu_actions["pills_manage"].shortcut().toString() == "Ctrl+Shift+P"
    hero.menu_actions["pills_manage"].trigger()
    assert w._pill_mgr is not None and w._pill_mgr.isVisible()
    w._pill_mgr.reject()
    QTest.keyClick(w, Qt.Key_P, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert w._pill_mgr.isVisible()
    w._pill_mgr.reject()


def test_the_tray_comes_after_the_title_and_before_the_buttons(hero):
    w = hero
    bar = w.quiet_bar
    lay = bar._lay
    order = [lay.itemAt(i).widget() for i in range(lay.count()) if lay.itemAt(i).widget() is not None]
    assert order[0] is bar._title and order[1] is w.pill_tray


def test_a_tray_with_no_pills_is_empty(win):
    assert win.pill_tray.capsules() == [] and win.pill_tray.plan(10) == []
    assert win.pill_tray.full_width() == 0 and win.pill_tray.minimumSizeHint().height() == win.pill_tray.height()


def test_the_plan_collapses_in_the_decided_order(win):
    add_pills(win, NAMES9)
    tray = win.pill_tray
    full = tray.full_width()
    p = tray.plan(full)
    assert len([c for c in p if c.kind == "pill"]) == 9 and not any(c.short for c in p)
    short = tray.plan(full - 5)
    assert len([c for c in short if c.kind == "pill"]) == 9 and all(c.short for c in short if c.kind == "pill")
    width = max(c.rect.right() for c in short) + 1
    count = tray.plan(width - 1)
    kinds = [c.kind for c in count]
    assert "edit" not in kinds and kinds[-1] == "more"
    shown = len([c for c in count if c.kind == "pill"])
    more = next(c for c in count if c.kind == "more")
    assert more.label == f"+{9 - shown}" and shown < 9
    assert max(c.rect.right() for c in count) + 1 <= width - 1
    narrow = tray.plan(10)
    assert "edit" not in [c.kind for c in narrow] and all(c.kind in ("more",) for c in narrow)  # no Edit capsule, ever


def test_the_width_the_edit_capsule_took_goes_to_the_pills(win):
    add_pills(win, NAMES9)
    tray = win.pill_tray
    need = max(c.rect.right() for c in tray.plan(10000) if c.kind == "pill") + 1 + 4  # + the trailing GAP  # the last pill's right edge
    assert not any(c.short for c in tray.plan(need))  # names still fit at exactly that width
    assert tray.full_width() == need


def test_more_capsule_opens_the_pills_menu(win, monkeypatch):
    add_pills(win, NAMES9)
    opened = []
    monkeypatch.setattr(win._pills_menu, "popup", lambda pos: opened.append(pos))
    win.pill_tray.more.emit()
    assert len(opened) == 1


RIGHT = ("btn_back", "btn_next", "btn_mark", "btn_zoom", "btn_review", "quietMore", "btn_copy")


@pytest.mark.parametrize("dark", [True, False], ids=["dark", "light"])
@pytest.mark.parametrize("width", [1280, 1440])
def test_the_tray_never_pushes_the_right_hand_buttons_off(tmp_path, width, dark):
    """MEASURE: 9 pills with 8-letter names at 1280 and 1440 logical px, both themes (printed with -s)."""
    theme.apply(APP, dark)
    w = make_window(tmp_path / "card", size=(width, 800))
    try:
        w.retheme()
        add_pills(w, NAMES9)
        QApplication.processEvents()
        tray, bar = w.pill_tray, w.quiet_bar
        caps = tray.capsules()
        pills = [c for c in caps if c.kind == "pill"]
        mode = "full" if not any(c.short for c in pills) and len(pills) == 9 else (
            "short" if len(pills) == 9 else f"count(+{9 - len(pills)})")
        btns = {b.objectName(): b for b in bar.findChildren(QAbstractButton) if b.objectName() in RIGHT}
        assert set(btns) == set(RIGHT) and all(b.isVisible() for b in btns.values())
        left_of_buttons = min(b.mapTo(w, QPoint(0, 0)).x() for b in btns.values())
        right_edge = max(b.mapTo(w, QPoint(b.width(), 0)).x() for b in btns.values())
        tray_right = tray.mapTo(w, QPoint(tray.width(), 0)).x()
        print(f"MEASURE {'dark' if dark else 'light'} {width}px: tray width {tray.width()} px, needs {tray.full_width()} px "
              f"in full, mode {mode}, {len(pills)}/9 pills shown, tray right edge {tray_right}, buttons {left_of_buttons}"
              f"..{right_edge}, window {w.width()}")
        assert w.width() == width
        assert tray_right <= left_of_buttons and right_edge <= width  # the tray stops short of the buttons
        assert max(c.rect.right() for c in caps) < tray.width()  # every capsule is inside the tray
        assert all(c.kind in ("pill", "more") for c in caps)
    finally:
        w.close()


@pytest.mark.parametrize("dark", [True, False], ids=["dark", "light"])
def test_the_tray_renders_in_both_themes(tmp_path, dark):
    theme.apply(APP, dark)
    w = make_window(tmp_path / "card")
    try:
        w.retheme()
        add_pills(w)
        QTest.keyClick(w, Qt.Key_1)  # Hero (Blue) on: its dot is drawn at full strength
        QApplication.processEvents()
        cap = next(c for c in w.pill_tray.capsules() if c.label == "Hero")
        img = w.pill_tray.grab().toImage()
        px = img.pixelColor(cap.rect.x() + 14, cap.rect.center().y())
        want = QColor(pills_mod.swatch("Blue").dark if dark else pills_mod.swatch("Blue").light)
        assert (px.red(), px.green(), px.blue()) == pytest.approx((want.red(), want.green(), want.blue()), abs=3)
        bg = img.pixelColor(img.width() - 2, img.height() // 2)
        assert bg.alpha() >= 0  # a real pixmap
        assert (px.red(), px.green(), px.blue()) != (bg.red(), bg.green(), bg.blue())
    finally:
        w.close()


# -- dots, inspector -------------------------------------------------------------------------------------
def test_filmstrip_dots_are_bottom_right_and_never_top_right(hero):
    w = hero
    w.pill_index.set(w.catalog.pictures[0], ["Hero", "Delivered"])
    w._pills_changed([w.catalog.pictures[0]])
    assert w.filmstrip.pill_colors(0) == ["Blue", "Green"]
    QApplication.processEvents()
    img = w.filmstrip.viewport().grab().toImage()
    cell = w.filmstrip.visualRect(w.filmstrip.model().index(0)).adjusted(4, 4, -4, -4)
    box_right, box_bottom, box_top = cell.x() + 104, cell.y() + 62, cell.y()
    blue, green = (QColor(pills_mod.swatch(n).dark).rgb() for n in ("Blue", "Green"))

    def hits(rgb, y0, y1):
        return [1 for x in range(box_right - 34, box_right) for y in range(y0, y1) if img.pixelColor(x, y).rgb() == rgb]

    assert hits(blue, box_bottom - 18, box_bottom) and hits(green, box_bottom - 18, box_bottom)
    assert not hits(blue, box_top, box_top + 20) and not hits(green, box_top, box_top + 20)


def test_filmstrip_dots_use_the_dark_swatch_in_the_light_theme_too(tmp_path):
    theme.apply(APP, False)
    w = make_window(tmp_path / "card")
    try:
        w.retheme()
        add_pills(w)
        w.pill_index.set(w.catalog.pictures[0], ["Hero"])
        w._pills_changed([w.catalog.pictures[0]])
        QApplication.processEvents()
        img = w.filmstrip.viewport().grab().toImage()
        cell = w.filmstrip.visualRect(w.filmstrip.model().index(0)).adjusted(4, 4, -4, -4)
        dark_blue = QColor(pills_mod.swatch("Blue").dark).rgb()
        assert any(img.pixelColor(x, y).rgb() == dark_blue
                   for x in range(cell.x() + 70, cell.x() + 104) for y in range(cell.y() + 44, cell.y() + 62))
    finally:
        w.close()


def test_the_inspector_lists_the_pills(hero):
    w = hero
    assert "Pills:" not in w.inspector.plain()
    QTest.keyClick(w, Qt.Key_1)
    QTest.keyClick(w, Qt.Key_2)
    assert w.inspector.plain().endswith("Pills: • Hero • Delivered")
    assert "Hero" in w.inspector.label.text() and "Delivered" in w.inspector.label.text()
    QTest.keyClick(w, Qt.Key_0)
    assert "Pills:" not in w.inspector.plain()


# -- the manager ------------------------------------------------------------------------------------------
def test_manager_lists_pills_with_counts_and_digits(hero):
    w = hero
    QTest.keyClick(w, Qt.Key_1)
    QTest.keyClick(w, Qt.Key_Right)
    QTest.keyClick(w, Qt.Key_1)
    w.open_pill_manager()
    m = w._pill_mgr
    assert [p.name for p in m.book.pills()] == ["Hero", "Delivered", "Edit"]
    assert m.count_fn("Hero") == 2 and m.count_fn("Delivered") == 0
    assert m.used.text() == "2 of 3 pictures in this folder"
    assert m.digits.digit == 1 and m.swatches.sel == "Blue"
    assert m.finder.text() == "Written as the Finder tag colour “Blue”"
    m.reject()


def test_manager_create_rename_recolor_delete_reach_the_index(hero):
    w = hero
    pic0 = w.catalog.pictures[0]
    QTest.keyClick(w, Qt.Key_2)  # Delivered on picture 0
    w.open_pill_manager()
    m = w._pill_mgr
    m._select(1)  # Delivered
    m.name.setText("Sent out")
    m._commit_name()
    assert w.pill_book.by_name("Sent out") and w.pill_book.by_name("Delivered") is None
    assert w.pill_index.get(pic0) == ("Sent out",)  # the rename reached the index
    assert w.pill_tray.capsules()[1].label == "Sent out" and w.pill_tray.capsules()[1].lit
    m.swatches.picked.emit("Red")
    assert w.pill_book.by_name("Sent out").color == "Red"
    assert w.pill_tray.capsules()[1].color == "Red"
    m.new_pill()
    settle()
    assert [p.name for p in w.pill_book.pills()][-1] == "New pill" and w.pill_book.by_name("New pill").digit == 4
    assert m.selected_pill().name == "New pill"
    m._select(1)
    m.ask_delete()
    assert m.confirming
    assert m.warn.text() == "Delete “Sent out”? It comes off 1 picture. Copies already made keep their tag."
    m.confirm_delete()
    assert w.pill_book.by_name("Sent out") is None and w.pill_index.get(pic0) == ()  # the delete reached the index
    assert not m.confirming
    m.reject()


def test_manager_rename_to_a_taken_name_is_refused_inline(hero):
    w = hero
    w.open_pill_manager()
    m = w._pill_mgr
    m.name.setText("delivered")
    m._commit_name()
    assert w.pill_book.by_name("Hero") is not None and m.name.text() == "Hero"
    assert "already" in m.problem.text()
    m.name.setText("   ")
    m._commit_name()
    assert w.pill_book.by_name("Hero") and "name" in m.problem.text()
    m.reject()


def test_manager_stops_at_nine_pills(win):
    add_pills(win, NAMES9[:8])
    win.open_pill_manager()
    m = win._pill_mgr
    assert m.new_button.isEnabled()
    m.new_pill()
    assert len(win.pill_book.pills()) == 9 and not m.new_button.isEnabled()
    m.new_pill()  # a no-op at the cap
    assert len(win.pill_book.pills()) == 9
    m._select(0)
    m.ask_delete()
    m.confirm_delete()
    assert len(win.pill_book.pills()) == 8 and m.new_button.isEnabled()
    m.reject()


def test_manager_enter_is_done_and_confirms_a_delete_and_esc_cancels_it(hero):
    w = hero
    w.open_pill_manager()
    m = w._pill_mgr
    m.ask_delete()
    QTest.keyClick(m, Qt.Key_Escape)
    assert m.isVisible() and not m.confirming and len(w.pill_book.pills()) == 3
    m.ask_delete()
    QTest.keyClick(m, Qt.Key_Return)
    assert m.isVisible() and len(w.pill_book.pills()) == 2
    QTest.keyClick(m, Qt.Key_Return)
    assert not m.isVisible()


def test_every_visible_manager_button_names_its_key(hero):
    w = hero
    w.open_pill_manager()
    m = w._pill_mgr
    for confirming in (False, True):
        if confirming:
            m.ask_delete()
        buttons = [b for b in m.findChildren(QAbstractButton) if b.isVisible()]
        assert {b.text() for b in buttons} >= ({"New pill", "Keep it", "Delete pill"} if confirming
                                               else {"New pill", "Delete…", "Done"})
        for b in buttons:
            tip = b.toolTip().replace("\xa0", " ")
            assert "  " in tip and tip.split("  ", 1)[1].strip() == b.cap, (b.text(), b.toolTip())
            assert b.chip.text() == b.cap and b.chip.isVisible()
    m.reject()


def test_manager_changes_are_saved_and_the_menu_follows(hero):
    w = hero
    w.open_pill_manager()
    m = w._pill_mgr
    m.name.setText("Star")
    m._commit_name()
    assert w._pill_dirty and w._pill_save_timer.isActive()
    assert w.menu_actions["pill_1"].text() == "Star"
    m.reject()


# -- the menu and the overlay -----------------------------------------------------------------------------
def test_pills_menu_sits_between_picture_and_help(win):
    assert [a.text() for a in win.menuBar().actions()] == ["File", "Edit", "View", "Picture", "Pills", "Stars", "Help"]
    assert list(act.MENUS) == ["File", "Edit", "View", "Picture", "Pills", "Stars", "Help"] and "Pills" in act.GROUPS


def test_menu_shows_names_and_check_states_and_hides_unused_digits(hero):
    w = hero
    qa = w.menu_actions
    assert [qa[f"pill_{n}"].isVisible() for n in range(1, 10)] == [True, True, True] + [False] * 6
    assert [qa[f"pill_{n}"].text() for n in (1, 2, 3)] == ["Hero", "Delivered", "Edit"]
    assert not qa["pill_1"].icon().isNull() and qa["pill_4"].icon().isNull()
    assert all(qa[f"pill_{n}"].isCheckable() for n in range(1, 10))
    assert not qa["pill_1"].isChecked()
    QTest.keyClick(w, Qt.Key_1)
    assert qa["pill_1"].isChecked() and not qa["pill_2"].isChecked()
    assert qa["pill_clear"].isEnabled() and qa["pills_manage"].isEnabled()
    qa["pill_2"].trigger()  # a click runs the same handler as the key
    assert set(names_of(w)) == {"Hero", "Delivered"} and qa["pill_2"].isChecked()
    qa["pill_clear"].trigger()
    assert names_of(w) == () and not qa["pill_1"].isChecked()
    QTest.keyClick(w, Qt.Key_Right)
    assert not qa["pill_1"].isChecked()  # follows the picture


def test_menu_row_shows_its_key(hero):
    for n in (1, 2, 3):
        qa = hero.menu_actions[f"pill_{n}"]
        assert qa.shortcut().toString() == str(n) and not qa.shortcut().isEmpty()
    assert hero.menu_actions["pills_manage"].shortcut().toString() == "Ctrl+Shift+P"
    assert hero.menu_actions["pills_manage"] in hero._pills_menu.actions()


def test_an_ampersand_in_a_pill_name_is_shown_as_is(win):
    add_pills(win, ["R&D"])
    assert win.menu_actions["pill_1"].text() == "R&&D"  # Qt shows && as one &


def test_the_overlay_lists_the_pills_group(hero):
    w = hero
    w.toggle_shortcuts()
    texts = w._shortcuts.labels()
    assert "PILLS" in texts
    for n, name in ((1, "Hero"), (2, "Delivered"), (3, "Edit")):
        assert name in texts and act.native_keys(act.by_id()[f"pill_{n}"]) in texts
    assert "Clear all pills" in texts and "Manage pills…" in texts
    assert act.native_keys(act.by_id()["pills_manage"]) in texts  # its key as this platform writes it
    assert "1 - 9 / 0" in texts and any("not in crop or story" in t for t in texts)
    assert "Pill 4" not in texts  # a digit with no pill has no row
    w.toggle_shortcuts()


def test_every_pill_action_has_a_handler_a_key_and_a_group():
    ids = [a.id for a in act.ACTIONS if a.group == "Pills"]
    assert ids == [f"pill_{n}" for n in range(1, 10)] + ["pill_clear", "pills_manage"]
    for a in act.ACTIONS:
        if a.group == "Pills":
            assert a.menu == "Pills" and a.keys and hasattr(MainWindow, a.handler), a.id


# -- read-back --------------------------------------------------------------------------------------------
def test_a_reloaded_folder_shows_the_pills_its_files_carry(tmp_path):
    tags = {0: [xmp.PillTag("Hero", "#2fb4e8"), xmp.PillTag("Delivered", "#8fd14f")], 2: [xmp.PillTag("Hero", "#2fb4e8")]}
    w = make_window(tmp_path / "copies", n=3, tags=tags)
    try:
        assert w.wait_pill_readback()
        assert [p.name for p in w.pill_book.pills()] == ["Hero", "Delivered"]
        assert [p.color for p in w.pill_book.pills()] == ["Blue", "Green"]
        assert names_of(w, 0) == ("Hero", "Delivered") and names_of(w, 1) == () and names_of(w, 2) == ("Hero",)
        pump(lambda: [c.lit for c in w.pill_tray.capsules() if c.kind == "pill"] == [True, True])
        assert w.inspector.plain().endswith("Pills: • Hero • Delivered")
        assert w.filmstrip.pill_colors(0) == ["Blue", "Green"] and w.filmstrip.pill_colors(1) == []
        assert w._pill_dirty  # the adopted pills are kept for next time
    finally:
        w.close()
    assert (tmp_path / "pills" / "pills.json").is_file() and (tmp_path / "pills" / "index.json").is_file()


def test_the_file_wins_over_the_index(tmp_path):
    folder = tmp_path / "copies"
    w = make_window(folder, n=1)
    add_pills(w, ["Hero", "Old"])
    w.pill_index.set(w.catalog.pictures[0], ["Old"])
    w.close()
    new, _ = xmp.embed((folder / "P0.jpg").read_bytes(), ".jpg", [xmp.PillTag("Hero", "#2fb4e8")])
    (folder / "P0.jpg").write_bytes(new)
    w2 = make_window(folder, n=1, make=False)
    try:
        assert w2.wait_pill_readback()
        assert names_of(w2) == ("Hero",)
    finally:
        w2.close()


def test_a_file_without_pills_leaves_the_index_alone(tmp_path):
    folder = tmp_path / "card"
    w = make_window(folder, n=2)
    add_pills(w)
    QTest.keyClick(w, Qt.Key_1)
    w.close()
    w2 = make_window(folder, n=2, make=False)
    try:
        assert w2.wait_pill_readback()
        assert names_of(w2) == ("Hero",)  # the card was never written: the app-data index remembers it
    finally:
        w2.close()


def test_a_late_result_from_an_older_load_is_ignored(win):
    old = win._pill_gen
    win.start_pill_readback()
    assert win._pill_gen == old + 1
    win._on_pill_batch(old, [(0, [xmp.PillTag("Ghost", "#ff4d4d")])])
    win._on_pill_finished(old, 1)
    assert win.pill_book.by_name("Ghost") is None and names_of(win) == ()
    assert win.wait_pill_readback()


def test_starting_a_new_read_cancels_the_old_one(win):
    first = win._pill_cancel
    win.start_pill_readback()
    assert first.is_set() and not win._pill_cancel.is_set()
    assert win.wait_pill_readback()


def test_a_hand_change_during_the_read_wins_over_the_older_file(tmp_path):
    folder = tmp_path / "copies"
    w = make_window(folder, n=1, tags={0: [xmp.PillTag("Hero", "#2fb4e8")]})
    try:
        assert w.wait_pill_readback() and names_of(w) == ("Hero",)  # the file's pill is in
        w.pill_book.create("Other", "Red")
        w.pill_index.set(w.catalog.pictures[0], ["Other"])
        w._pills_changed([w.catalog.pictures[0]])  # ... then Daniel changes it by hand
        w._on_pill_batch(w._pill_gen, [(0, [xmp.PillTag("Hero", "#2fb4e8")])])  # an older read arrives late
        assert names_of(w) == ("Other",)
    finally:
        w.close()


def test_the_readback_is_bounded_and_fast_on_500_pictures(tmp_path):
    folder = tmp_path / "big"
    tags = {i: [xmp.PillTag("Hero", "#2fb4e8")] for i in range(0, 500, 5)}
    make_pictures(folder, 500, tags)
    cat = Catalog(scan(folder))
    assert len(cat) == 500
    w = MainWindow(cat, Prefetcher(cat), folder)
    try:
        t0 = time.perf_counter()
        w.start_pill_readback()
        assert w.wait_pill_readback(30000)
        ms = (time.perf_counter() - t0) * 1000
        print(f"READBACK 500 JPEGs (100 with pills): {ms:.0f} ms")
        assert ms < 30000
        assert w.pill_counts() == {"hero": 100}
        from spuza.ui import window as winmod

        assert winmod.PILL_READ_MAX >= 500 and winmod.PILL_READ_BATCH > 0
    finally:
        w.close()


def test_the_readback_stops_at_its_cap(tmp_path, monkeypatch):
    from spuza.ui import window as winmod

    monkeypatch.setattr(winmod, "PILL_READ_MAX", 2)
    folder = tmp_path / "card"
    w = make_window(folder, n=4, tags={i: [xmp.PillTag("Hero", "#2fb4e8")] for i in range(4)})
    try:
        assert w.wait_pill_readback()
        assert w.pill_counts() == {"hero": 2}
    finally:
        w.close()


# -- persistence ------------------------------------------------------------------------------------------
def test_pills_survive_a_new_window_on_the_same_pills_dir(tmp_path):
    folder = tmp_path / "card"
    w = make_window(folder)
    add_pills(w)
    QTest.keyClick(w, Qt.Key_2)
    w.close()
    w2 = make_window(folder, make=False)
    try:
        assert [p.name for p in w2.pill_book.pills()] == ["Hero", "Delivered", "Edit"]
        assert names_of(w2) == ("Delivered",)
        assert [c.lit for c in w2.pill_tray.capsules() if c.kind == "pill"] == [False, True, False]
        assert w2.wait_pill_readback()
        assert w2.filmstrip.pill_colors(0) == ["Green"]
    finally:
        w2.close()


def test_saves_are_debounced_and_written_on_flush(hero, tmp_path):
    w = hero
    index_file = tmp_path / "pills" / "index.json"
    QTest.keyClick(w, Qt.Key_1)
    assert w._pill_save_timer.isActive() and w._pill_dirty and not index_file.exists()  # nothing written yet
    w._flush_pill_save()
    w._pill_save_pool.waitForDone(5000)
    assert index_file.is_file() and (tmp_path / "pills" / "pills.json").is_file() and not w._pill_dirty


def test_close_writes_what_the_timer_has_not(hero, tmp_path):
    QTest.keyClick(hero, Qt.Key_1)
    assert not (tmp_path / "pills" / "index.json").exists()
    hero.close()
    assert pills_mod.PillIndex.load().get(hero.catalog.pictures[0]) == ("Hero",)
    assert [p.name for p in pills_mod.PillBook.load().pills()] == ["Hero", "Delivered", "Edit"]


def test_a_window_that_changed_nothing_writes_nothing(win, tmp_path):
    win.close()
    assert not (tmp_path / "pills").exists()


def test_a_failing_save_never_reaches_the_ui(hero, monkeypatch):
    def boom(self):
        raise OSError("disk full")

    monkeypatch.setattr(pills_mod.PillBook, "save", boom)
    monkeypatch.setattr(pills_mod.PillIndex, "save", boom)
    QTest.keyClick(hero, Qt.Key_1)
    hero._flush_pill_save()
    hero._pill_save_pool.waitForDone(5000)
    QTest.keyClick(hero, Qt.Key_2)
    hero.close()  # the close path swallows it too
    assert names_of(hero) == ("Hero", "Delivered")


# -- copy -------------------------------------------------------------------------------------------------
def test_copy_passes_the_marked_pictures_pills(hero):
    w = hero
    got = {}

    class Sheet:
        def show(self):
            pass

    def factory(pictures, default_dest=None, parent=None, **kw):
        got.update(kw)
        got["pictures"] = pictures
        return Sheet()

    w.copy_sheet_factory = factory
    for i in (0, 1):
        w.catalog.toggle_mark(i)
    QTest.keyClick(w, Qt.Key_1)  # picture 0: Hero
    QTest.keyClick(w, Qt.Key_Right)
    QTest.keyClick(w, Qt.Key_2)  # picture 1: Delivered
    QTest.keyClick(w, Qt.Key_3)  # ... and Edit
    w.open_copy()
    assert set(got["pills"]) == {w.catalog.pictures[0].primary, w.catalog.pictures[1].primary}
    assert got["pills"][w.catalog.pictures[0].primary] == [w.pill_book.tag("Hero")]
    assert got["pills"][w.catalog.pictures[1].primary] == [w.pill_book.tag("Delivered"), w.pill_book.tag("Edit")]
    assert got["pills"][w.catalog.pictures[0].primary][0] == xmp.PillTag("Hero", "#2fb4e8")


def test_copy_without_pills_does_not_pass_the_kwarg(hero):
    seen = {}

    def factory(pictures, default_dest=None, parent=None, **kw):
        seen.update(kw)

        class Sheet:
            def show(self):
                pass

        return Sheet()

    hero.copy_sheet_factory = factory
    hero.catalog.toggle_mark(0)
    hero.open_copy()
    assert "pills" not in seen


def test_copy_skips_pills_the_book_no_longer_knows(hero):
    w = hero
    QTest.keyClick(w, Qt.Key_1)
    w.pill_book.delete("Hero")  # deleted from the book but still named in the index
    assert w._copy_pills(w.catalog.pictures) == {}


def _copy_sheet_takes_pills() -> bool:
    from spuza.ui.copy_sheet import CopySheet

    return "pills" in inspect.signature(CopySheet.__init__).parameters


@pytest.mark.skipif(not _copy_sheet_takes_pills(), reason="needs spp-copy merged: CopySheet(pills=...)")
def test_the_default_factory_gives_the_real_copy_sheet_the_pills(hero, tmp_path):
    w = hero
    QTest.keyClick(w, Qt.Key_1)
    w.catalog.toggle_mark(0)
    w.open_copy()
    try:
        assert w._sheet is not None and w._sheet.isVisible()
    finally:
        w._sheet.close()
