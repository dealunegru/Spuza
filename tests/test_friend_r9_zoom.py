"""Friend round 9: moving to another picture while zoomed keeps the level AND the point looked at (a fraction of
the picture as shown), so 2-3 shots of one frame land on the same detail. Synthetic pictures only."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza import derived as dv
from spuza.ui.viewer import Viewer, ZoomMemory, turn_frac


def pump(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        QTest.qWait(5)
    QApplication.processEvents()
    return cond()


def solid(w=400, h=300) -> QImage:
    im = QImage(w, h, QImage.Format.Format_RGB888)
    im.fill(QColor("#336699"))
    return im


def pic_of(name):
    return SimpleNamespace(primary=Path(name), stem=Path(name).stem)


def info(w=1600, h=1200):
    return SimpleNamespace(width=w, height=h)


def make_viewer(native=(1600, 1200), preview=(400, 300), rotations=None):
    v = Viewer()
    v.resize(800, 600)
    v.show()
    v.needs_full_decode = lambda p, i: True
    v.full_decode = lambda p, half=False: Image.new("RGB", native)
    if rotations is not None:
        v.rotation_of = lambda i: rotations.get(i, 0)
    v.show_image(0, pic_of("a.jpg"), solid(*preview), info(*native))
    return v


def looking_at(v):
    """The viewport centre as a fraction of the picture AS SHOWN (turns included), read from the scene."""
    c = v.mapToScene(v.viewport().rect().center())
    r = v._scene.sceneRect()
    return ((c.x() - r.x()) / r.width(), (c.y() - r.y()) / r.height())


def look_at(v, fx, fy):
    r = v._scene.sceneRect()
    v.centerOn(QPointF(r.x() + fx * r.width(), r.y() + fy * r.height()))


def close(v):
    v.shutdown()
    v.close()


def test_position_carries_to_the_next_picture():
    v = make_viewer()
    v.zoom_in()
    look_at(v, 0.2, 0.7)
    want = looking_at(v)
    v.show_image(1, pic_of("b.jpg"), solid(600, 450), info(2400, 1800))  # another size, same aspect
    assert v.zoomed
    assert looking_at(v) == pytest.approx(want, abs=0.01)
    close(v)


def test_position_carries_through_a_placeholder_and_an_error():
    v = make_viewer()
    v.zoom_in()
    look_at(v, 0.8, 0.3)
    want = looking_at(v)
    v.show_placeholder(1, pic_of("b.jpg"))
    v.show_error(1, pic_of("b.jpg"), "boom")
    v.show_image(1, pic_of("b.jpg"), solid(), info())
    assert looking_at(v) == pytest.approx(want, abs=0.01)
    close(v)


def test_remembered_position_wins_over_the_focus_point():
    v = make_viewer()
    v.zoom_target = lambda: (0.9, 0.9)
    v.zoom_in()
    look_at(v, 0.35, 0.35)
    v.show_image(1, pic_of("b.jpg"), solid(), info())
    assert looking_at(v) == pytest.approx((0.35, 0.35), abs=0.01)
    close(v)


def test_first_zoom_still_goes_to_the_focus_point_and_fit_stays_fit():
    v = make_viewer()
    v.zoom_target = lambda: (0.65, 0.65)
    v.show_image(1, pic_of("b.jpg"), solid(), info())  # at fit: nothing to carry
    assert not v.zoomed
    v.zoom_in()
    assert looking_at(v) == pytest.approx((0.65, 0.65), abs=0.02)
    v.zoom_out()
    assert not v.zoomed
    look_at(v, 0.5, 0.5)
    v.show_image(2, pic_of("c.jpg"), solid(), info())
    assert not v.zoomed
    v.zoom_in()  # a fresh zoom after fit forgets the old position
    assert looking_at(v) == pytest.approx((0.65, 0.65), abs=0.02)
    close(v)


def test_position_follows_the_turn_between_pictures():
    rot = {0: 0, 1: 1, 2: 3, 3: 2}
    v = make_viewer(rotations=rot)
    v.zoom_in()
    look_at(v, 0.46, 0.54)  # near the middle: a turned picture is narrower, so the edges clamp
    want = looking_at(v)
    for i in (1, 2, 3, 0):
        v.show_image(i, pic_of(f"p{i}.jpg"), solid(400, 300), info())
        assert looking_at(v) == pytest.approx(want, abs=0.02), i
    close(v)


def test_position_survives_the_native_decode_swap():
    v = make_viewer()
    v.zoom_in()
    assert pump(lambda: v.full_state(0) == "done")
    look_at(v, 0.3, 0.6)
    want = looking_at(v)
    v.show_image(1, pic_of("b.jpg"), solid(), info())
    assert pump(lambda: v.full_state(1) == "done")
    assert looking_at(v) == pytest.approx(want, abs=0.02)
    close(v)


def test_cached_native_picture_also_lands_on_the_position():
    v = make_viewer()
    v.zoom_in()
    assert pump(lambda: v.full_state(0) == "done")
    v.show_image(1, pic_of("b.jpg"), solid(), info())
    assert pump(lambda: v.full_state(1) == "done")
    look_at(v, 0.15, 0.4)
    want = looking_at(v)
    v.show_image(0, pic_of("a.jpg"), solid(), info())  # a.jpg's full decode is cached: native at once
    assert v._shown_native
    assert looking_at(v) == pytest.approx(want, abs=0.02)
    close(v)


def test_derived_crop_row_does_not_crash_and_keeps_the_fraction_of_what_is_shown():
    v = make_viewer()
    v.zoom_in()
    look_at(v, 0.4, 0.6)
    d = dv.Derived(id=5, source=0, kind=dv.CROP, crop=(0.1, 0.1, 0.9, 0.9))
    v.show_image(1, pic_of("a.jpg"), solid(), info(), derived=d)
    assert looking_at(v) == pytest.approx((0.4, 0.6), abs=0.02)
    v.show_image(2, pic_of("c.jpg"), solid(), info())  # and back to a plain picture
    assert looking_at(v) == pytest.approx((0.4, 0.6), abs=0.02)
    close(v)


def test_story_row_with_turns_does_not_crash():
    v = make_viewer(rotations={0: 1})
    v.zoom_in()
    look_at(v, 0.4, 0.4)
    v.show_image(1, pic_of("b.jpg"), solid(), info())
    assert v.zoomed and v.has_image
    close(v)


def test_leaving_zoom_forgets_the_position():
    v = make_viewer()
    v.zoom_in()
    look_at(v, 0.1, 0.1)
    v.zoom_out()
    assert v._zoom_memory.frac is None
    close(v)


def test_message_screen_does_not_break_the_memory():
    v = make_viewer()
    v.zoom_in()
    look_at(v, 0.6, 0.6)
    v.show_message("no pictures")
    v.show_image(1, pic_of("b.jpg"), solid(), info())
    assert looking_at(v) == pytest.approx((0.6, 0.6), abs=0.02)
    close(v)


# ---- the reusable helper --------------------------------------------------------------------------------

def test_turn_frac_quarters_and_inverse():
    assert turn_frac((0.25, 0.75), 0) == (0.25, 0.75)
    assert turn_frac((0.25, 0.75), 1) == pytest.approx((0.25, 0.25))
    assert turn_frac((0.25, 0.75), 2) == pytest.approx((0.75, 0.25))
    assert turn_frac((0.25, 0.75), 3) == pytest.approx((0.75, 0.75))
    for q in range(4):
        back = turn_frac(turn_frac((0.1, 0.9), q), -q)
        assert back == pytest.approx((0.1, 0.9))


def test_zoom_memory_roundtrip_and_bad_input():
    m = ZoomMemory()
    assert m.recall(0) is None
    m.remember((0.25, 0.75), 1)  # own-frame fraction of a picture turned once
    assert m.frac == pytest.approx(turn_frac((0.25, 0.75), 1))
    assert m.recall(1) == pytest.approx((0.25, 0.75))
    assert m.recall(0) == pytest.approx(turn_frac((0.25, 0.75), 1))
    for bad in (None, (0.5,), "ab", (float("nan"), 0.5), (0.5, float("inf")), ("x", 1), 7):
        m.remember(bad, 0)
        assert m.recall(0) is None
    m.remember((2.0, -1.0), 0)  # clamped into the picture
    assert m.recall(0) == (1.0, 0.0)
    m.forget()
    assert m.frac is None
