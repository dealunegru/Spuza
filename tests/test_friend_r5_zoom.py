"""Friend round 5: + / = and - step the zoom in 25% levels of the picture's own pixels (fit, 25% .. 400%), in the
viewer and on the review zoom page; Z keeps its toggle. Synthetic pictures only."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui.review import ReviewGrid
from spuza.ui.viewer import ZOOM_STEPS, Viewer, step_scale
from spuza.ui.window import MainWindow

ROOT = Path("/cards/X")


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


def pic_of(name="a.jpg"):
    return SimpleNamespace(primary=Path(name), stem=Path(name).stem)


def make_viewer(native=(1600, 1200), preview=(400, 300), threads=None):
    """A viewer showing a capped preview of a picture whose native size is `native`; the full decode is a stub."""
    v = Viewer()
    v.resize(800, 600)
    v.show()
    v.needs_full_decode = lambda p, i: True

    def full(p, half=False):
        if threads is not None:
            threads.append(threading.current_thread())
        return Image.new("RGB", native)

    v.full_decode = full
    v.show_image(0, pic_of(), solid(*preview), SimpleNamespace(width=native[0], height=native[1]))
    return v


def scale_of(v, native_w):
    """Picture pixels per device pixel as drawn now, whatever pixmap is on screen."""
    return v.transform().m11() * v.devicePixelRatioF() * v.image_size()[0] / native_w


def test_step_scale_table():
    assert ZOOM_STEPS[0] == 0.25 and ZOOM_STEPS[-1] == 4.0 and len(ZOOM_STEPS) == 16
    assert step_scale(0.133, 0.133, True) == 0.25
    assert step_scale(0.5, 0.5, True) == 0.75  # strictly above
    assert step_scale(2.4, 2.4, True) == 2.5
    assert step_scale(4.0, 0.5, True) is None
    assert step_scale(1.0, 0.5, False) == 0.75
    assert step_scale(0.75, 0.5, False) == 0.0  # 0.5 is at fit: back to fit
    assert step_scale(0.25, 0.133, False) == 0.0
    assert step_scale(1.0, 2.4, False) == 0.0  # Z's 100% on a small picture: below it is fit
    assert step_scale(1.25, 0.1, False) == 1.0


def test_plus_from_fit_goes_to_first_step_above_fit_and_minus_steps_back():
    v = make_viewer()
    assert v.fit_scale() == pytest.approx(0.5, rel=0.02)
    seen = []
    v.zoom_changed.connect(seen.append)
    v.zoom_in()
    assert v.zoomed and scale_of(v, 1600) == pytest.approx(0.75)
    v.zoom_in()
    assert scale_of(v, 1600) == pytest.approx(1.0)
    v.zoom_out()
    assert scale_of(v, 1600) == pytest.approx(0.75)
    v.zoom_out()
    assert not v.zoomed and seen == [True, False]
    v.shutdown()
    v.close()


def test_small_fit_scale_below_25_percent_first_step_is_25():
    v = make_viewer(native=(6000, 4000), preview=(300, 200))
    assert v.fit_scale() < 0.25
    v.zoom_in()
    assert scale_of(v, 6000) == pytest.approx(0.25)
    v.shutdown()
    v.close()


def test_small_picture_fit_above_100_percent_first_step_is_above_fit():
    v = make_viewer(native=(300, 200), preview=(300, 200))
    assert v.fit_scale() == pytest.approx(2.67, abs=0.05)  # 800 / 300
    v.zoom_in()
    assert scale_of(v, 300) == pytest.approx(2.75)
    v.zoom_out()
    assert not v.zoomed  # 2.5 is below fit
    v.shutdown()
    v.close()


def test_floor_and_ceiling_do_nothing():
    v = make_viewer()
    v.zoom_out()
    assert not v.zoomed and v.hud_visible is False
    for _ in range(40):
        v.zoom_in()
    assert v.zoom_scale == 4.0 and v.zoom_label() == "400%"
    v.zoom_in()
    assert v.zoom_scale == 4.0 and v.zoomed
    v.shutdown()
    v.close()


def test_z_from_a_step_level_goes_to_fit_and_from_fit_to_100():
    v = make_viewer()
    v.zoom_in()
    v.toggle_zoom()
    assert not v.zoomed and v.zoom_text() == "" and not v.hud_visible
    v.toggle_zoom()
    assert v.zoomed and v.zoom_scale == 1.0 and v.zoom_label() == "100%"
    v.zoom_in()
    assert v.zoom_scale == 1.25
    v.toggle_zoom()
    assert not v.zoomed
    v.shutdown()
    v.close()


def test_hud_text_per_level():
    v = make_viewer()
    v.zoom_in()  # 75%, native pixels still loading
    assert v.hud_visible and v.zoom_text() == "75% … decoding full resolution…"
    assert pump(lambda: v.full_state(0) == "done")
    assert v.zoom_text() == "75%" and v.hud_visible and v.hud.text() == "75%"
    v.zoom_in()
    v.zoom_in()
    assert v.zoom_text() == "125%" and v.hud_visible
    v._jpg_bad.add("a.jpg")
    v._sync_hud()
    assert v.zoom_text() == "125% · JPG unreadable, from the raw"
    v._jpg_bad.clear()
    v._shown_sensor = True
    assert v.zoom_text() == "125% · full sensor"
    v.shutdown()
    v.close()


def test_z_hud_is_untouched_by_the_new_levels():
    v = make_viewer()
    v.toggle_zoom()
    assert v.zoom_text() == "100% … decoding full resolution…" and v.hud_visible
    assert pump(lambda: v.full_state(0) == "done")
    assert v.zoom_text() == "100%" and not v.hud_visible
    v.shutdown()
    v.close()


def test_first_step_decodes_native_pixels_off_the_gui_thread_once():
    threads = []
    v = make_viewer(threads=threads)
    v.zoom_in()
    assert v.full_state(0) == "pending"
    assert pump(lambda: v.full_state(0) == "done")
    assert len(threads) == 1 and threads[0] is not threading.main_thread()
    assert v.image_size() == (1600, 1200)
    v.zoom_in()
    v.zoom_out()
    QApplication.processEvents()
    assert len(threads) == 1  # later steps reuse the native pixels
    v.shutdown()
    v.close()


def test_preview_is_scaled_to_the_native_size_and_the_swap_does_not_jump():
    v = make_viewer()
    v.zoom_in()
    assert v.image_size() == (400, 300) and scale_of(v, 1600) == pytest.approx(0.75)
    before = v.mapFromScene(v._item.sceneBoundingRect().center())
    on_screen = v._item.sceneBoundingRect().width() * v.transform().m11()
    assert pump(lambda: v.full_state(0) == "done")
    assert v.image_size() == (1600, 1200)
    assert v._item.sceneBoundingRect().width() * v.transform().m11() == pytest.approx(on_screen, rel=0.01)
    after = v.mapFromScene(v._item.sceneBoundingRect().center())
    assert abs(after.x() - before.x()) <= 2 and abs(after.y() - before.y()) <= 2
    v.shutdown()
    v.close()


def test_centre_is_kept_across_steps_and_the_swap():
    v = make_viewer()
    v.zoom_in()
    assert pump(lambda: v.full_state(0) == "done")
    pm = v._item.pixmap()
    v.centerOn(v._item.mapToScene(QPointF(0.4 * pm.width(), 0.55 * pm.height())))
    a = v._centre_frac()
    v.zoom_in()
    b = v._centre_frac()
    v.zoom_out()
    c = v._centre_frac()
    assert a[0] == pytest.approx(0.4, abs=0.02) and a[1] == pytest.approx(0.55, abs=0.02)
    for got in (b, c):
        assert got[0] == pytest.approx(a[0], abs=0.02) and got[1] == pytest.approx(a[1], abs=0.02)
    v.shutdown()
    v.close()


def test_centre_is_kept_through_the_swap_to_native_pixels():
    v = make_viewer()
    v.zoom_in()
    pm = v._item.pixmap()
    v.centerOn(v._item.mapToScene(QPointF(0.25 * pm.width(), 0.25 * pm.height())))
    a = v._centre_frac()
    assert pump(lambda: v.full_state(0) == "done")
    b = v._centre_frac()
    assert b[0] == pytest.approx(a[0], abs=0.02) and b[1] == pytest.approx(a[1], abs=0.02)
    v.shutdown()
    v.close()


def test_level_carries_to_the_next_picture():
    v = make_viewer()
    v.zoom_in()
    assert pump(lambda: v.full_state(0) == "done")
    v.zoom_in()
    v.show_image(1, pic_of("b.jpg"), solid(), SimpleNamespace(width=1600, height=1200))
    assert v.zoomed and v.zoom_scale == 1.0 and scale_of(v, 1600) == pytest.approx(1.0)
    assert v.zoom_text().startswith("100%")
    v.shutdown()
    v.close()


def test_no_picture_no_step():
    v = Viewer()
    v.resize(800, 600)
    v.show()
    v.zoom_in()
    v.zoom_out()
    assert not v.zoomed
    v.close()


# ---- the review zoom page -------------------------------------------------------------------------------

class _Cat:
    def __init__(self):
        self.pictures = [Picture("P0", ROOT / "P0.JPG", None, (ROOT / "P0.JPG",), "JPG", 1, 1.0)]
        self.marked_count = 1
        self.marked_bytes = 1

    def __len__(self):
        return 1

    def is_marked(self, i):
        return True

    def set_mark(self, i, on):
        pass


def make_review(native=(1600, 1200)):
    calls = []

    def loader(path, max_side):
        calls.append(max_side)
        if max_side is None:
            return Image.new("RGB", native), SimpleNamespace(native=True)
        return Image.new("RGB", (400, 300)), SimpleNamespace(native=True)

    class Pf:
        def cached(self, i):
            return solid(400, 300)

        def info(self, i):
            return SimpleNamespace(width=native[0], height=native[1])

    g = ReviewGrid(_Cat(), Pf(), loader=loader)
    g.resize(800, 600)
    g.show()
    g.view.setCurrentIndex(g.model.index(0, 0))
    g.show_zoom()
    return g, calls


def r_scale(g, native_w):
    pm = g._zoom_item.pixmap()
    return g.zoom_view.transform().m11() * g.zoom_view.devicePixelRatioF() * pm.width() / native_w


def test_review_zoom_page_steps_and_caption():
    g, calls = make_review()
    fit = g._fit_scale()
    g.zoom_step(True)
    first = g._zoom_scale
    assert not g._zoom_fit and g._zoom_pct and first in ZOOM_STEPS and first > fit
    assert [s for s in ZOOM_STEPS if s > fit][0] == first
    label = f"{round(first * 100)}%"
    assert r_scale(g, 1600) == pytest.approx(first, abs=0.02)
    assert f"{label} … decoding full resolution…" in g.zoom_caption.text()
    assert pump(lambda: g._zoom_native)
    assert None in calls and label in g.zoom_caption.text() and "…" not in g.zoom_caption.text().split("·")[-1]
    while g._zoom_scale < 1.25:
        g.zoom_step(True)
    assert g._zoom_scale == 1.25 and "125%" in g.zoom_caption.text()
    assert r_scale(g, 1600) == pytest.approx(1.25, abs=0.02)
    for _ in range(20):
        g.zoom_step(False)
    assert g._zoom_fit and g.zoom_caption.text().split("   (Z")[0].endswith(" B")
    g.zoom_step(False)  # the floor
    assert g._zoom_fit
    g.wait_idle()
    g.close()


def test_review_z_toggle_still_means_100_and_resets_the_level():
    g, _ = make_review()
    g.zoom_step(True)
    g.toggle_zoom()
    assert g._zoom_fit
    g.toggle_zoom()
    assert not g._zoom_fit and g._zoom_scale == 1.0 and not g._zoom_pct
    assert pump(lambda: g._zoom_native)
    assert g.zoom_caption.text().count("100%") >= 1 and "(Z: fit / 100%" in g.zoom_caption.text()
    g.zoom_step(True)
    assert g._zoom_scale == 1.25
    g.wait_idle()
    g.close()


def test_review_ceiling_and_grid_page_do_nothing():
    g, _ = make_review()
    for _ in range(40):
        g.zoom_step(True)
    assert g._zoom_scale == 4.0
    g.leave_zoom()
    g.zoom_step(True)
    g.zoom_step(False)
    assert g._zoom_scale == 4.0 and g.stack.currentIndex() != 2
    g.wait_idle()
    g.close()


def test_review_keys_plus_equal_minus():
    g, _ = make_review()
    QTest.keyClick(g.zoom_view, Qt.Key_Plus, Qt.KeyboardModifier.ShiftModifier)
    assert g._zoom_pct
    first = g._zoom_scale
    QTest.keyClick(g.zoom_view, Qt.Key_Equal)
    assert g._zoom_scale > first
    QTest.keyClick(g.zoom_view, Qt.Key_Minus)
    assert g._zoom_scale == first
    g.wait_idle()
    g.close()


# ---- window routing -------------------------------------------------------------------------------------

def _loader(path, max_side):
    return Image.new("RGB", (64, 48), (90, 120, 200)), PreviewInfo(path, "pillow", 64, 48, 10, 1.0)


@pytest.fixture
def win():
    pics = [Picture(f"P{i}", ROOT / f"P{i}.JPG", None, (ROOT / f"P{i}.JPG",), "JPG", 1_000_000, 1.0)
            for i in range(4)]
    cat = Catalog(pics)
    w = MainWindow(cat, Prefetcher(cat, loader=_loader), ROOT)
    w.resize(1000, 700)
    w.show()
    w.activateWindow()
    QTest.qWaitForWindowActive(w, 3000)
    assert pump(lambda: w.viewer.has_image)
    yield w
    w.close()


KEYS = [(Qt.Key_Plus, Qt.KeyboardModifier.ShiftModifier, "in"), (Qt.Key_Plus, Qt.KeyboardModifier.KeypadModifier, "in"),
        (Qt.Key_Equal, Qt.KeyboardModifier.NoModifier, "in"), (Qt.Key_Minus, Qt.KeyboardModifier.NoModifier, "out"),
        (Qt.Key_Minus, Qt.KeyboardModifier.KeypadModifier, "out")]


@pytest.mark.parametrize("key,mod,which", KEYS)
def test_window_routes_zoom_keys_to_the_viewer(win, key, mod, which):
    calls = []
    win.viewer.zoom_in = lambda: calls.append("in")
    win.viewer.zoom_out = lambda: calls.append("out")
    QTest.keyClick(win, key, mod)
    assert calls == [which]


def test_ctrl_minus_is_not_a_zoom_key(win):
    calls = []
    win.viewer.zoom_out = lambda: calls.append("out")
    QTest.keyClick(win, Qt.Key_Minus, Qt.KeyboardModifier.ControlModifier)
    assert calls == []


@pytest.mark.parametrize("key,mod,which", KEYS)
def test_window_routes_zoom_keys_to_the_review_zoom_page(win, key, mod, which):
    for i in (0, 1):
        win.catalog.set_mark(i, True)
    win.refresh_marks()
    win.buttons["review"].click()
    assert win.stack.currentIndex() == 1
    got = []
    win.review.zoom_step = lambda up: got.append(up)
    viewer_calls = []
    win.viewer.zoom_in = lambda: viewer_calls.append("in")
    win.viewer.zoom_out = lambda: viewer_calls.append("out")
    QTest.keyClick(win, key, mod)
    assert got == [which == "in"] and viewer_calls == []


def test_grid_page_ignores_zoom_keys_and_does_not_drive_the_hidden_viewer(win):
    win.catalog.set_mark(0, True)
    win.refresh_marks()
    win.buttons["review"].click()
    assert win.review.stack.currentIndex() != 2
    viewer_calls = []
    win.viewer.zoom_in = lambda: viewer_calls.append("in")
    QTest.keyClick(win, Qt.Key_Plus, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(win, Qt.Key_Minus)
    assert viewer_calls == [] and win.review.stack.currentIndex() != 2
