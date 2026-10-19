"""Friend round 6, window side: the ratio strip, keys 1-8 / V / 0 while the X crop edit is open, the stored ratio,
the inspector rows and the filmstrip tags. Synthetic pictures only; the loader's 64 x 48 preview is told to be a
6000 x 4000 picture (the design table's stand-in) through the prefetcher's info."""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton

from spuza import ratio
from spuza.ratio import FREE, Ratio
from spuza.story import Story
from spuza.ui.inspector import edit_lines
from spuza.ui.ratio_strip import Chip, RatioStrip
from test_friend_r3_window import make_window, pump

CHIPS = ["1 Free", "2 Original", "3 1:1", "4 4:5", "5 3:4", "6 2:3", "7 9:16", "8 1.91:1", "V turn"]
FREE_STATUS = "Crop free: drag on the picture. 1-8 ratio, 0 resets. Enter applies, Esc cancels, X clears"


def status(label):
    return (f"Crop {label}: drag to move, corners to size. 1-8 ratio, V turns it, 0 resets. "
            "Enter applies, Esc cancels, X clears")


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


def key(win, k):
    QTest.keyClick(win, k)


def px(win):
    """The overlay's label: "<ratio> · W × H px"."""
    return win._crop_overlay.label_text()


def chips(win):
    return win.ratio_strip.chip_texts()


# -- the strip -------------------------------------------------------------------------------------------------
def test_strip_is_hidden_until_x_and_gone_after(w):
    assert not w.ratio_strip.isVisible() and w.ratio_strip.isHidden()
    key(w, Qt.Key_X)
    assert w.ratio_strip.isVisible()
    assert chips(w) == CHIPS
    key(w, Qt.Key_Escape)
    assert not w.ratio_strip.isVisible()
    key(w, Qt.Key_X)
    key(w, Qt.Key_Return)
    assert not w.ratio_strip.isVisible()
    key(w, Qt.Key_S)
    assert not w.ratio_strip.isVisible()  # the story fit has no strip: S is as in 0.8.1


def test_strip_sits_above_the_picture_and_is_42_px(w):
    key(w, Qt.Key_X)
    strip, vw = w.ratio_strip, w.viewer
    assert strip.height() == 42
    assert strip.mapTo(w, strip.rect().bottomLeft()).y() <= vw.mapTo(w, vw.rect().topLeft()).y() + 1


def test_the_current_chip_shows_its_on_screen_label(w):
    key(w, Qt.Key_X)
    assert chips(w) == CHIPS
    key(w, Qt.Key_4)
    assert chips(w)[3] == "4 4:5" and w.ratio_strip.current_entry() == 4
    key(w, Qt.Key_V)
    assert chips(w)[3] == "4 5:4"  # the actual orientation
    key(w, Qt.Key_8)
    assert chips(w)[3] == "4 4:5" and chips(w)[7] == "8 1.91:1"
    key(w, Qt.Key_V)
    assert chips(w)[7] == "8 1:1.91"


def test_the_strip_never_takes_focus(w):
    strip = w.ratio_strip
    assert strip.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert all(b.focusPolicy() == Qt.FocusPolicy.NoFocus for b in strip.findChildren(Chip))
    key(w, Qt.Key_X)
    QTest.mouseClick(strip._chips[4], Qt.MouseButton.LeftButton)
    assert not strip.hasFocus() and not any(b.hasFocus() for b in strip.findChildren(Chip))
    assert not strip.findChildren(QAbstractButton)  # not review controls: test_ui_review_controls counts buttons


@pytest.mark.parametrize("dark", [True, False])
def test_strip_uses_theme_colours_light_and_dark(w, dark):
    from spuza.ui import theme

    theme._current = theme.DARK if dark else theme.LIGHT
    try:
        w.ratio_strip.retheme()
        c = theme.current()
        css = w.ratio_strip.styleSheet()
        assert c.panel in css and c.border in css
        key(w, Qt.Key_X)
        key(w, Qt.Key_4)
        assert c.accent in w.ratio_strip._chips[4].styleSheet()  # the current chip is accent-filled
        assert c.accent not in w.ratio_strip._chips[5].styleSheet()
        key(w, Qt.Key_Escape)
    finally:
        theme._current = theme.DARK


def test_a_click_does_what_the_key_does(w):
    key(w, Qt.Key_X)
    w.ratio_strip._chips[4].click()
    assert px(w) == "4:5 · 3200 × 4000 px" and w.last_message == status("4:5")
    w.ratio_strip._turn.click()
    assert px(w) == "5:4 · 4000 × 3200 px"  # D5: the same centre and area, turned
    w.ratio_strip._chips[1].click()
    assert w.last_message == FREE_STATUS and w._crop_overlay.aspect is None


# -- keys ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("k,label,pixels", [
    (Qt.Key_2, "Original", "6000 × 4000"),
    (Qt.Key_3, "1:1", "4000 × 4000"),
    (Qt.Key_4, "4:5", "3200 × 4000"),
    (Qt.Key_5, "3:4", "3000 × 4000"),
    (Qt.Key_6, "2:3", "2667 × 4000"),
    (Qt.Key_7, "9:16", "2250 × 4000"),
    (Qt.Key_8, "1.91:1", "6000 × 3144"),
])
def test_digits_open_the_largest_centred_rectangle(w, k, label, pixels):
    key(w, Qt.Key_X)
    key(w, k)
    assert px(w) == f"{label} · {pixels} px"


@pytest.mark.parametrize("k,label,pixels", [
    (Qt.Key_4, "5:4", "5000 × 4000"),
    (Qt.Key_5, "4:3", "5333 × 4000"),
    (Qt.Key_6, "3:2", "6000 × 4000"),
    (Qt.Key_7, "16:9", "6000 × 3375"),
    (Qt.Key_8, "1:1.91", "2096 × 4000"),
])
def test_v_turns_the_ratio(w, k, label, pixels):
    key(w, Qt.Key_X)
    key(w, k)
    before = w._crop_overlay.rect_frac()
    key(w, Qt.Key_V)
    after = w._crop_overlay.rect_frac()
    assert px(w).startswith(f"{label} · ") and w._crop_ratio == Ratio(k - Qt.Key_0, True)
    cx = lambda r: ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)  # noqa: E731
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])  # noqa: E731
    assert cx(after) == pytest.approx(cx(before), abs=1e-3)  # the same centre
    assert area(after) <= area(before) * 1.01  # the same area, shrunk only where the picture forces it
    key(w, Qt.Key_0)
    assert px(w) == f"{label} · {pixels} px"  # V then 0: the design table's largest crop of the turned ratio
    key(w, Qt.Key_V)
    assert w._crop_ratio == Ratio(k - Qt.Key_0)


def test_v_does_nothing_for_free_and_square(w):
    key(w, Qt.Key_X)
    key(w, Qt.Key_V)
    assert w._crop_ratio == FREE
    key(w, Qt.Key_3)
    key(w, Qt.Key_V)
    assert w._crop_ratio == Ratio(3) and px(w) == "1:1 · 4000 × 4000 px"


def test_a_ratio_keeps_the_centre_and_the_area_of_the_rectangle(w):
    key(w, Qt.Key_X)
    w._crop_overlay.set_rect_frac((0.2, 0.2, 0.6, 0.6))  # centre (0.4, 0.4); 2400 x 1600 px
    key(w, Qt.Key_4)
    left, top, right, bottom = w._crop_overlay.rect_frac()
    assert ((left + right) / 2, (top + bottom) / 2) == pytest.approx((0.4, 0.4), abs=1e-3)
    assert (right - left) * 6000 * (bottom - top) * 4000 == pytest.approx(2400 * 1600, rel=0.01)
    assert (right - left) * 6000 / ((bottom - top) * 4000) == pytest.approx(0.8, rel=0.01)


def test_zero_resets_to_the_largest_rectangle_of_the_current_ratio(w):
    key(w, Qt.Key_X)
    key(w, Qt.Key_4)
    w._crop_overlay.set_rect_frac((0.0, 0.0, 0.2, 0.25))
    key(w, Qt.Key_0)
    assert px(w) == "4:5 · 3200 × 4000 px"
    key(w, Qt.Key_V)
    w._crop_overlay.set_rect_frac((0.0, 0.0, 0.1, 0.1))
    key(w, Qt.Key_0)
    assert px(w) == "5:4 · 5000 × 4000 px"


def test_nine_and_other_keys_do_nothing_in_the_edit(w):
    key(w, Qt.Key_X)
    key(w, Qt.Key_4)
    rect, msg = w._crop_overlay.rect_frac(), w.last_message
    key(w, Qt.Key_9)
    assert w._crop_overlay.rect_frac() == rect and w._crop_ratio == Ratio(4) and w.last_message == msg
    assert w._edit == "crop"


def test_auto_repeat_is_ignored(w):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    key(w, Qt.Key_X)
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_4, Qt.KeyboardModifier.NoModifier, "4", True)
    w.keyPressEvent(ev)
    assert w._crop_ratio == FREE
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_4, Qt.KeyboardModifier.NoModifier, "4", False)
    w.keyPressEvent(ev)
    assert w._crop_ratio == Ratio(4)


def test_digits_and_v_do_nothing_on_the_plain_viewer(w):
    msg = w.last_message
    for k in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4, Qt.Key_5, Qt.Key_6, Qt.Key_7, Qt.Key_8, Qt.Key_9, Qt.Key_V,
              Qt.Key_0):
        key(w, k)
    assert w._edit is None and w._crop_overlay is None and w.crop_ratios == {} and w.crops == {}
    assert not w.ratio_strip.isVisible() and w.last_message == msg


def test_digits_and_v_do_nothing_in_story_fit(w):
    key(w, Qt.Key_S)
    before = w._story_editor.story()
    for k in (Qt.Key_1, Qt.Key_4, Qt.Key_8, Qt.Key_9, Qt.Key_V):
        key(w, k)
    assert w._story_editor.story() == before and w._story_editor.active
    assert w._crop_overlay is None and w.crop_ratios == {} and not w.ratio_strip.isVisible()
    key(w, Qt.Key_Plus)
    key(w, Qt.Key_0)  # S keeps only its 0
    assert w._story_editor.story() == Story()


# -- status line -----------------------------------------------------------------------------------------------
def test_status_line_names_the_ratio(w):
    key(w, Qt.Key_X)
    assert w.last_message == FREE_STATUS
    key(w, Qt.Key_4)
    assert w.last_message == status("4:5")
    key(w, Qt.Key_V)
    assert w.last_message == status("5:4")
    key(w, Qt.Key_1)
    assert w.last_message == FREE_STATUS


# -- storage ---------------------------------------------------------------------------------------------------
def test_enter_stores_the_ratio_and_reopening_restores_it(w):
    key(w, Qt.Key_X)
    key(w, Qt.Key_4)
    key(w, Qt.Key_V)
    key(w, Qt.Key_0)
    key(w, Qt.Key_Return)
    assert w.crop_ratios == {0: Ratio(4, True)} and 0 in w.crops
    assert "Cropped P0: 5:4, 5000 x 4000 px" in w.last_message
    key(w, Qt.Key_X)
    assert w._crop_ratio == Ratio(4, True) and chips(w)[3] == "4 5:4"
    assert w._crop_overlay.aspect == pytest.approx(1.25)
    assert px(w) == "5:4 · 5000 × 4000 px"
    key(w, Qt.Key_Escape)
    assert w.crop_ratios == {0: Ratio(4, True)}


def test_free_is_absent_and_a_cancelled_edit_keeps_the_old_ratio(w):
    key(w, Qt.Key_X)
    key(w, Qt.Key_5)
    key(w, Qt.Key_Return)
    key(w, Qt.Key_X)
    key(w, Qt.Key_1)
    key(w, Qt.Key_Escape)
    assert w.crop_ratios == {0: Ratio(5)}
    key(w, Qt.Key_X)
    key(w, Qt.Key_1)
    key(w, Qt.Key_Return)
    assert w.crop_ratios == {} and 0 in w.crops


def test_each_picture_has_its_own_ratio(w):
    key(w, Qt.Key_X)
    key(w, Qt.Key_4)
    key(w, Qt.Key_Return)
    w.step(1)
    pump(lambda: w.prefetcher.cached(1) is not None and w.viewer.has_image)  # X needs the picture decoded
    key(w, Qt.Key_X)
    assert w._crop_ratio == FREE and w.last_message == FREE_STATUS
    key(w, Qt.Key_7)
    key(w, Qt.Key_Return)
    assert w.crop_ratios == {0: Ratio(4), 1: Ratio(7)}


def test_x_again_and_backspace_drop_the_ratio_too(w):
    for clear in (Qt.Key_X, Qt.Key_Backspace):
        key(w, Qt.Key_X)
        key(w, Qt.Key_4)
        key(w, Qt.Key_Return)
        key(w, Qt.Key_X)
        key(w, clear)
        assert w.crops == {} and w.crop_ratios == {} and not w.ratio_strip.isVisible()


def test_backspace_on_the_viewer_clears_both_and_says_so(w):
    key(w, Qt.Key_X)
    key(w, Qt.Key_4)
    key(w, Qt.Key_Return)
    key(w, Qt.Key_S)
    key(w, Qt.Key_Return)
    assert w.crops and w.stories and w.crop_ratios
    key(w, Qt.Key_Backspace)
    assert w.crops == {} and w.stories == {} and w.crop_ratios == {}
    assert w.last_message == "Cleared the crop and the story fit on P0"
    key(w, Qt.Key_X)
    key(w, Qt.Key_3)
    key(w, Qt.Key_Return)
    key(w, Qt.Key_Backspace)
    assert w.last_message == "Cleared the crop on P0"


def test_backspace_in_the_crop_edit_keeps_the_story(w):
    key(w, Qt.Key_S)
    key(w, Qt.Key_Return)
    key(w, Qt.Key_X)
    key(w, Qt.Key_4)
    key(w, Qt.Key_Return)
    key(w, Qt.Key_X)
    key(w, Qt.Key_Backspace)
    assert w.crops == {} and list(w.stories) == [0]


def test_a_crop_and_a_story_coexist_with_their_ratio(w):
    key(w, Qt.Key_S)
    key(w, Qt.Key_Return)
    key(w, Qt.Key_X)
    key(w, Qt.Key_4)
    key(w, Qt.Key_Return)
    assert list(w.crops) == [0] and list(w.stories) == [0] and w.crop_ratios == {0: Ratio(4)}


# -- turned pictures -------------------------------------------------------------------------------------------
def test_a_turned_picture_opens_the_ratio_on_screen_and_names_it_as_seen(w):
    w.rotations[0] = 1
    key(w, Qt.Key_X)
    key(w, Qt.Key_4)
    assert w._crop_ratio == Ratio(4, True)  # stored in the un-turned frame
    assert chips(w)[3] == "4 4:5" and w.last_message == status("4:5")  # portrait on screen
    key(w, Qt.Key_Return)
    assert w.inspector.lines.count("Crop: 4:5, 4000 x 5000 px") == 1  # on-screen pixel order
    assert w.filmstrip.edit_tag_of(0) == [("4:5", "crop")]


# -- inspector -------------------------------------------------------------------------------------------------
CROP_4X5 = ratio.largest_centred(0.8, 6000, 4000)


@pytest.mark.parametrize("r,turns,expect", [
    (Ratio(4), 0, "Crop: 4:5, 3200 x 4000 px"),
    (Ratio(4, True), 0, "Crop: 5:4, 5000 x 4000 px"),
    (FREE, 0, "Crop: free, 3720 x 2480 px"),
    (Ratio(2), 0, "Crop: original, 6000 x 4000 px"),
])
def test_edit_lines_for_the_design_examples(r, turns, expect):
    native = (6000, 4000)
    if r.entry == 1:
        crop = (0.0, 0.0, 0.62, 0.62)
    else:
        crop = ratio.largest_centred(ratio.frame_aspect(r, *native), *native)
    assert edit_lines(crop, None, r, native, turns) == [expect]


def test_edit_lines_story_unchanged_and_both_crop_first():
    assert edit_lines(None, Story(1.4, 0, 0)) == ["Story fit: 9:16, zoom 1.4x"]
    both = edit_lines(CROP_4X5, Story(1.4, 0, 0), Ratio(4), (6000, 4000), 0)
    assert both == ["Crop: 4:5, 3200 x 4000 px", "Story fit: 9:16, zoom 1.4x"]
    assert edit_lines() == []


@pytest.mark.parametrize("bad", [None, "x", 5, (1, 2), ("a", 1, 2, 3), float("nan")])
def test_edit_lines_survive_junk(bad):
    assert isinstance(edit_lines(bad, bad, bad, bad, 0), list)
    assert isinstance(edit_lines((0.1, 0.1, 0.5, 0.5), None, None, bad, bad if isinstance(bad, int) else 0), list)


def test_inspector_shows_both_lines_in_the_window(w):
    key(w, Qt.Key_X)
    key(w, Qt.Key_4)
    key(w, Qt.Key_Return)
    key(w, Qt.Key_S)
    key(w, Qt.Key_Return)
    lines = [t for t in w.inspector.lines if t.startswith(("Crop", "Story"))]
    assert lines == ["Crop: 4:5, 3200 x 4000 px", "Story fit: 9:16, zoom 1.0x"]
    assert w.inspector.lines[-1] in ("Marked", "Not marked")


# -- filmstrip -------------------------------------------------------------------------------------------------
def test_filmstrip_tags_carry_the_ratio_and_the_story_is_an_outline(w):
    key(w, Qt.Key_X)
    key(w, Qt.Key_4)
    key(w, Qt.Key_Return)
    assert w.filmstrip.edit_tags(0) == [("4:5", "crop")]
    key(w, Qt.Key_X)
    key(w, Qt.Key_V)
    key(w, Qt.Key_Return)
    assert w.filmstrip.edit_tags(0) == [("5:4", "crop")]
    key(w, Qt.Key_X)
    key(w, Qt.Key_1)
    key(w, Qt.Key_Return)
    assert w.filmstrip.edit_tags(0) == [("crop", "crop")]
    key(w, Qt.Key_X)
    key(w, Qt.Key_2)
    key(w, Qt.Key_Return)
    assert w.filmstrip.edit_tags(0) == [("crop", "crop")]  # Original keeps the plain "crop"
    key(w, Qt.Key_S)
    key(w, Qt.Key_Return)
    assert w.filmstrip.edit_tags(0) == [("crop", "crop"), ("9:16", "story")]  # two tags, side by side
    cell = w.filmstrip.visualRect(w.filmstrip.model().index(0))
    assert w.filmstrip.viewport().grab(cell).toImage().width() > 0  # paints both without raising


def test_the_strip_class_is_what_the_window_holds(w):
    assert isinstance(w.ratio_strip, RatioStrip)
