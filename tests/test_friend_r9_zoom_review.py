"""Friend round 9: on the review zoom page and in compare, moving to the next picture keeps the zoom level and the
look-at point (a fraction of the picture, through turns); fit stays fit; opening fresh starts at fit.
Synthetic pictures only."""

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

from spuza import features
from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui.compare import CompareView
from spuza.ui.review import ReviewGrid
from spuza.ui.viewer import turn_frac

ROOT = Path("/cards/X")
NATIVE = {0: (1600, 1200), 1: (2000, 1000), 2: (1200, 1600), 3: (1600, 1200)}  # picture 2 is a portrait


def pump(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        QTest.qWait(5)
    QApplication.processEvents()
    return cond()


def idx(path) -> int:
    return int(Path(path).stem[1:])


def shot(i, side=None):
    w, h = NATIVE[i]
    if side is not None:
        k = side / max(w, h)
        w, h = round(w * k), round(h * k)
    return Image.new("RGB", (w, h), (30 * i + 20, 100, 150))


def pics(n=4):
    return [Picture(f"P{i}", ROOT / f"P{i}.JPG", None, (ROOT / f"P{i}.JPG",), "JPG", 1000, 1.0) for i in range(n)]


# ---- review zoom page ------------------------------------------------------------------------------------

@pytest.fixture
def rv():
    calls = []

    def loader(path, max_side):
        calls.append((idx(path), max_side))
        return shot(idx(path), max_side or None), SimpleNamespace(native=True)

    class Pf:
        def cached(self, i):
            w, h = NATIVE[i]
            im = QImage(w // 4, h // 4, QImage.Format.Format_RGB888)
            im.fill(QColor("#336699"))
            return im

        def info(self, i):
            return SimpleNamespace(width=NATIVE[i][0], height=NATIVE[i][1])

    cat = Catalog(pics())
    for i in range(4):
        cat.set_mark(i, True)
    g = ReviewGrid(cat, Pf(), loader=loader)
    g.resize(500, 400)
    g.show()
    g.open_set("marked", None, prefer=0)
    yield g, calls
    g.wait_idle()
    g.leave_zoom()
    g.close()


def shown_frac(g):
    """Where the page centre is, as a fraction of the picture AS SHOWN (turns included)."""
    c = g.zoom_view.mapToScene(g.zoom_view.viewport().rect().center())
    r = g.scene.sceneRect()
    return ((c.x() - r.left()) / r.width(), (c.y() - r.top()) / r.height())


def look_at(g, own):
    pm = g._zoom_item.pixmap()
    g.zoom_view.centerOn(g._zoom_item.mapToScene(QPointF(own[0] * pm.width(), own[1] * pm.height())))


def zoomed_at(g, own=(0.4, 0.6)):
    g.toggle_zoom()  # Z: 100%
    assert pump(lambda: g._zoom_native)
    look_at(g, own)
    return own


def test_step_keeps_the_level_and_the_point(rv):
    g, calls = rv
    own = zoomed_at(g)
    g.zoom_step(True)  # a +/- level: 125%
    assert pump(lambda: g._zoom_native)
    look_at(g, own)
    g.step_set(+1)
    assert g._zoom_index == 1 and not g._zoom_fit
    assert g._zoom_scale == 1.25 and g._zoom_pct
    assert pump(lambda: g._zoom_native and g._native_img is not None and g._native_img[0] == 1)
    assert (1, None) in calls  # the native decode for the new picture was started
    fx, fy = shown_frac(g)
    assert (fx, fy) == pytest.approx(own, abs=0.02)
    assert g.zoom_view.transform().m11() * g.zoom_view.devicePixelRatioF() * g._zoom_item.pixmap().width() \
        / NATIVE[1][0] == pytest.approx(1.25, abs=0.02)


def test_step_back_also_carries(rv):
    g, _ = rv
    g.step_set(+1)
    own = zoomed_at(g, (0.6, 0.4))
    g.step_set(-1)
    assert g._zoom_index == 0 and not g._zoom_fit and g._zoom_scale == 1.0
    assert pump(lambda: g._zoom_native)
    assert shown_frac(g) == pytest.approx(own, abs=0.02)


def test_the_point_survives_the_decode_swap_and_a_fast_walk(rv):
    g, _ = rv
    own = zoomed_at(g)
    for _ in range(3):  # no waiting between the steps: the point is kept from picture to picture
        g.step_set(+1)
    assert g._zoom_index == 3 and not g._zoom_fit
    assert pump(lambda: g._zoom_native)
    assert shown_frac(g) == pytest.approx(own, abs=0.02)


def test_fit_stays_fit(rv):
    g, _ = rv
    g.step_set(+1)
    assert g._zoom_index == 1 and g._zoom_fit
    g.step_set(+1)
    assert g._zoom_fit and not g._zoom_pct


def test_back_to_fit_forgets_the_point(rv):
    g, _ = rv
    zoomed_at(g, (0.3, 0.3))
    g.toggle_zoom()  # Z back to fit
    g.step_set(+1)
    assert g._zoom_fit
    g.toggle_zoom()  # 100% again: the centre, not the old point
    assert pump(lambda: g._zoom_native)
    assert shown_frac(g) == pytest.approx((0.5, 0.5), abs=0.02)


def test_the_point_goes_through_a_turn(rv):
    g, _ = rv
    g.rotation_of = lambda i: 1 if i == 1 else 0  # picture 1 is shown turned a quarter clockwise
    own = zoomed_at(g, (0.4, 0.6))
    g.step_set(+1)
    assert pump(lambda: g._zoom_native)
    assert shown_frac(g) == pytest.approx(own, abs=0.02)  # the same spot of the picture as seen
    g.step_set(+1)  # and back to an unturned one
    assert pump(lambda: g._zoom_native)
    assert shown_frac(g) == pytest.approx(own, abs=0.02)
    assert turn_frac((0.4, 0.6), 1) != pytest.approx((0.4, 0.6))  # the turn really moved the own-frame point


def test_opening_the_set_fresh_starts_at_fit(rv):
    g, _ = rv
    zoomed_at(g)
    g.leave_zoom()
    g.open_set("marked", None, prefer=2)
    assert g._zoom_index == 2 and g._zoom_fit
    g.show_zoom(3)  # a pick from the grid is a fresh opening too
    assert g._zoom_fit


def test_the_ends_keep_the_picture_and_the_zoom(rv):
    g, _ = rv
    own = zoomed_at(g)
    g.step_set(-1)  # first picture: stays
    assert g._zoom_index == 0 and not g._zoom_fit
    assert shown_frac(g) == pytest.approx(own, abs=0.02)


# ---- compare ---------------------------------------------------------------------------------------------

class CmpLoader:
    def __call__(self, path, max_side):
        i = idx(path)
        im = shot(i, max_side or None)
        return im, PreviewInfo(path, "pillow", im.width, im.height, 10, 1.0)


@pytest.fixture
def cv():
    features.set_mode(features.ADVANCED)
    cat = Catalog(pics())
    ld = CmpLoader()
    pf = Prefetcher(cat, ahead=3, behind=0, loader=ld, threads=2)
    pf.update(0)
    pump(lambda: all(pf.cached(i) is not None for i in range(4)))
    v = CompareView(cat, pf, loader=ld)
    v.resize(1200, 420)
    v.show()
    QTest.qWait(30)
    yield v
    v.shutdown()
    pf.shutdown()
    v.close()


def pan(cell, dx=40, dy=25):
    cell.horizontalScrollBar().setValue(cell.horizontalScrollBar().value() + dx)
    cell.verticalScrollBar().setValue(cell.verticalScrollBar().value() + dy)


def test_compare_next_open_keeps_the_zoom_and_the_point(cv):
    cv.open([0, 1])
    cv.toggle_zoom()
    assert pump(lambda: all(c.zoom_img is not None for c in cv._cells))
    pan(cv.cell(0))
    here = cv.cell(0).centre_fraction()
    assert here != pytest.approx((0.5, 0.5), abs=0.01)
    cv.open([2, 3])  # the next pair
    assert cv.is_zoomed() and all(c.zoomed for c in cv._cells)
    assert pump(lambda: all(c.zoom_img is not None for c in cv._cells))
    for c in cv._cells:
        assert turn_frac(c.centre_fraction(), c.rotation) == pytest.approx(turn_frac(here, 0), abs=0.02)


def test_compare_fit_stays_fit(cv):
    cv.open([0, 1])
    cv.open([2, 3])
    assert not cv.is_zoomed() and not any(c.zoomed for c in cv._cells)


def test_compare_leaving_zoom_forgets(cv):
    cv.open([0, 1])
    cv.toggle_zoom()
    pan(cv.cell(0))
    cv.toggle_zoom()  # back to fit
    cv.open([2, 3])
    assert not cv.is_zoomed()


def test_compare_closing_forgets(cv):
    cv.open([0, 1])
    cv.toggle_zoom()
    pan(cv.cell(0))
    cv.close_compare()
    cv.open([2, 3])
    assert not cv.is_zoomed()


def test_compare_point_goes_through_a_turn(cv):
    cv.rotation_of = lambda i: 1 if i == 2 else 0
    cv.open([0, 1])
    cv.toggle_zoom()
    assert pump(lambda: all(c.zoom_img is not None for c in cv._cells))
    pan(cv.cell(0), 300, -150)
    here = cv.cell(0).centre_fraction()
    cv.open([2, 3])
    assert pump(lambda: all(c.zoom_img is not None for c in cv._cells))
    for c in cv._cells:  # the same spot of the picture as seen
        assert turn_frac(c.centre_fraction(), c.rotation) == pytest.approx(here, abs=0.03)
    assert cv.cell(2).centre_fraction() != pytest.approx(here, abs=0.05)
