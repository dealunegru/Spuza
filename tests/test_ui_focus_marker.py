"""FocusMarker: placement on the item's current pixmap, constant screen size, flag gating."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage

from spuza import features
from spuza.ui.focus_marker import RADIUS, FocusMarker
from spuza.ui.viewer import Viewer


def _img(w, h) -> QImage:
    im = QImage(w, h, QImage.Format.Format_RGB888)
    im.fill(0x336699)
    return im


def _pic():
    return SimpleNamespace(primary=Path("a"), stem="a")


def _viewer():
    v = Viewer()
    v.resize(800, 600)
    v.show()
    v.needs_full_decode = lambda p, i: False
    return v


def test_classic_set_on_is_noop():
    v = _viewer()
    m = FocusMarker(v)
    v.show_image(0, _pic(), _img(400, 300), object())
    m.set_point((0.5, 0.5))
    m.set_on(True)
    assert not m.is_on() and not m.visible()


def test_placement_follows_pixmap_size():
    features.set_mode(features.ADVANCED)
    v = _viewer()
    m = FocusMarker(v)
    m.set_on(True)
    m.set_point((0.25, 0.5))
    assert not m.visible()  # nothing shown yet
    v.show_image(0, _pic(), _img(400, 300), object())
    assert m.visible() and m.is_on()
    ring = v.image_item().childItems()[0]
    assert (ring.pos().x(), ring.pos().y()) == pytest.approx((100.0, 150.0))
    v._put(_img(1600, 1200), keep_centre=False)  # the full-resolution swap: a bigger pixmap
    assert (ring.pos().x(), ring.pos().y()) == pytest.approx((400.0, 600.0))


def test_constant_screen_size_at_fit_and_100():
    features.set_mode(features.ADVANCED)
    v = _viewer()
    m = FocusMarker(v)
    m.set_on(True)
    m.set_point((0.5, 0.5))
    v.show_image(0, _pic(), _img(2000, 1500), object())
    ring = v.image_item().childItems()[0]

    def on_screen_width():
        # sceneBoundingRect is unreliable for ItemIgnoresTransformations; ask for the device transform
        return ring.deviceTransform(v.viewportTransform()).mapRect(ring.boundingRect()).width()

    fit = on_screen_width()
    v.toggle_zoom()
    full = on_screen_width()
    assert fit == pytest.approx(full, abs=1.5) and fit >= RADIUS


def test_none_point_and_off_hide():
    features.set_mode(features.ADVANCED)
    v = _viewer()
    m = FocusMarker(v)
    v.show_image(0, _pic(), _img(400, 300), object())
    m.set_on(True)
    m.set_point((0.1, 0.1))
    assert m.visible()
    m.set_point(None)
    assert not m.visible()
    m.set_point((0.1, 0.1))
    m.set_on(False)
    assert not m.visible()
    m.set_on(True)
    v.show_placeholder(1, _pic())  # pixmap cleared
    assert not m.visible()
