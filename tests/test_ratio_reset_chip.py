"""The "0 Reset" chip on the ratio strip: it exists, names its key, and a click emits `reset`. End to end, with the
window's `_reset_ratio` connected to it (the window wiring line is pending), the rectangle returns to the largest
centred one. Synthetic pictures only (the 64 x 48 preview told to be 6000 x 4000, as in test_friend_r6_strip)."""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton

from spuza import ratio
from spuza.ui.ratio_strip import RatioStrip
from test_friend_r3_window import make_window


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


def test_chip_exists_and_names_its_key():
    strip = RatioStrip()
    assert strip._reset.text() == "0 Reset"
    assert "0 Reset" in strip.chip_texts()
    assert not strip.findChildren(QAbstractButton)


def test_click_emits_reset_once():
    strip = RatioStrip()
    got = []
    strip.reset.connect(lambda: got.append(1))
    strip._reset.click()
    assert got == [1]


def test_click_with_the_mouse_emits_reset():
    strip = RatioStrip()
    got = []
    strip.reset.connect(lambda: got.append(1))
    QTest.mouseClick(strip._reset, Qt.MouseButton.LeftButton)
    assert got == [1]


def test_reset_chip_is_styled_like_the_others():
    strip = RatioStrip()
    assert strip._reset.styleSheet() == strip._turn.styleSheet() != ""


def test_chip_returns_the_largest_centred_rectangle(w):
    QTest.keyClick(w, Qt.Key_X)
    QTest.keyClick(w, Qt.Key_4)
    cw, ch = w._crop_native
    largest = tuple(ratio.largest_centred(ratio.frame_aspect(w._crop_ratio, cw, ch), cw, ch))
    w._crop_overlay.set_rect_frac((0.1, 0.1, 0.5, 0.6))
    w._crop_overlay.changed.emit()
    assert w._crop_overlay.rect_frac() != largest
    assert w._crop_overlay.rect_frac() is not None
    w.ratio_strip._reset.click()
    assert tuple(w._crop_overlay.rect_frac()) == pytest.approx(largest)
    assert w._rect_auto is True


def test_real_window_wiring_mouse_click_resets_the_rectangle(w):
    """No hand-connect: the window itself must route the chip's `reset` to `_reset_ratio`."""
    QTest.keyClick(w, Qt.Key_X)
    QTest.keyClick(w, Qt.Key_4)
    cw, ch = w._crop_native
    largest = tuple(ratio.largest_centred(ratio.frame_aspect(w._crop_ratio, cw, ch), cw, ch))
    w._crop_overlay.set_rect_frac((0.1, 0.1, 0.5, 0.6))
    w._crop_overlay.changed.emit()
    assert w._rect_auto is False
    assert tuple(w._crop_overlay.rect_frac()) != pytest.approx(largest)
    QTest.mouseClick(w.ratio_strip._reset, Qt.MouseButton.LeftButton)
    assert tuple(w._crop_overlay.rect_frac()) == pytest.approx(largest)
    assert w._rect_auto is True
