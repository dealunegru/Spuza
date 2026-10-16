"""CompareView: focus, marks, zoom lock, N growth, stale decodes, and nothing at all in classic mode."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza import features
from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui import theme
from spuza.ui.compare import CompareView

ROOT = Path("/cards/X")
N = 8


def colour(i: int) -> tuple[int, int, int]:
    return (20 + 25 * i, 200 - 20 * i, 90)


def pic_index(path) -> int:
    return int(Path(path).stem[1:])


def size_for(i: int, max_side) -> tuple[int, int]:
    w, h = (300, 400) if i == 2 else (400, 300)  # picture 2 is a portrait
    return (w * 4, h * 4) if max_side is None else (w, h)


class Loader:
    """Fake preview.load: colour by picture, full size when max_side is None, records every call."""

    def __init__(self):
        self.calls: list[tuple[int, int | None]] = []
        self.gates: dict[tuple[int, int | None], threading.Event] = {}
        self.fail: set[int] = set()
        self._lock = threading.Lock()

    def __call__(self, path, max_side):
        i = pic_index(path)
        with self._lock:
            self.calls.append((i, max_side))
        gate = self.gates.get((i, max_side))
        if gate is not None:
            assert gate.wait(10)  # bounded: a test that forgets to release fails, never hangs
        if i in self.fail:
            raise ValueError("bad picture")
        w, h = size_for(i, max_side)
        return Image.new("RGB", (w, h), colour(i)), PreviewInfo(path, "pillow", w, h, 10, 1.0)


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond()


@pytest.fixture
def env():
    features.set_mode(features.ADVANCED)
    pics = [Picture(f"P{i}", ROOT / f"P{i}.JPG", None, (ROOT / f"P{i}.JPG",), "JPG", 1_000_000, 1.0)
            for i in range(N)]
    cat = Catalog(pics)
    ld = Loader()
    pf = Prefetcher(cat, ahead=3, behind=0, loader=ld, threads=2)
    pf.update(0)
    pump(lambda: all(pf.cached(i) is not None for i in range(4)))
    ld.calls.clear()
    view = CompareView(cat, pf, loader=ld)
    view.resize(1200, 420)
    view.show()
    QTest.qWait(30)
    yield cat, pf, ld, view
    view.shutdown()
    pf.shutdown()
    view.close()


def key(view, k, repeat=False):
    if repeat:
        ev = QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier, "", True, 1)
        QApplication.sendEvent(view, ev)
    else:
        QTest.keyClick(view, k)


def colour_at(cell, x=5, y=5):
    c = cell.fit_img.pixelColor(x, y)
    return (c.red(), c.green(), c.blue())


def test_classic_open_does_nothing(env):
    cat, pf, ld, view = env
    features.set_mode(features.CLASSIC)
    seen = []
    view.closed.connect(seen.append)
    view.open([0, 1])
    assert view.indices() == [] and view.focused_index() == -1
    assert ld.calls == [] and view.status_text() == "compare 0 · 0 marked"
    key(view, Qt.Key.Key_Z)
    key(view, Qt.Key.Key_Escape)
    assert seen == [] and ld.calls == []


def test_fitted_open_cached_makes_no_decode(env):
    cat, pf, ld, view = env
    view.open([0, 1, 2, 3])
    assert view.indices() == [0, 1, 2, 3]
    assert all(view.cell(i).fit_img is not None for i in range(4))
    view.wait_idle()
    assert ld.calls == []
    assert colour_at(view.cell(1)) == colour(1)


def test_open_cuts_to_four_and_dedupes(env):
    cat, pf, ld, view = env
    view.open([1, 1, 99, -1, 0, 2, 3, 5, 6])
    assert view.indices() == [1, 0, 2, 3]
    view.open([], 0)  # an empty pick keeps what is shown
    assert view.indices() == [1, 0, 2, 3]


def test_focus_keys_never_leave_the_set_and_border_follows(env):
    cat, pf, ld, view = env
    seen = []
    view.focus_changed.connect(seen.append)
    view.open([1, 2, 3], focus=0)
    acc = theme.current().accent
    assert view.focused_index() == 1 and view.border_colour(1) == acc and view.border_colour(2) == "transparent"
    key(view, Qt.Key.Key_Left)
    assert view.focused_index() == 1
    key(view, Qt.Key.Key_Right)
    key(view, Qt.Key.Key_J)  # J = next (right), as in the window
    key(view, Qt.Key.Key_Right)
    assert view.focused_index() == 3 and view.border_colour(3) == acc and view.border_colour(1) == "transparent"
    key(view, Qt.Key.Key_K)  # K = back (left)
    assert view.focused_index() == 2
    assert seen == [1, 2, 3, 2]


def test_mark_goes_to_the_shared_catalog(env):
    cat, pf, ld, view = env
    hits = []
    view.marks_changed.connect(lambda: hits.append(1))
    view.open([1, 2, 3], focus=1)
    key(view, Qt.Key.Key_Space)
    assert cat.is_marked(2) and not cat.is_marked(1) and len(hits) == 1
    assert view.status_text() == "compare 3 · 1 marked"
    key(view, Qt.Key.Key_M)
    assert not cat.is_marked(2) and len(hits) == 2
    key(view, Qt.Key.Key_M, repeat=True)  # a held key must not flip it again
    key(view, Qt.Key.Key_Space, repeat=True)
    assert not cat.is_marked(2) and len(hits) == 2


def test_advance_on_mark_moves_and_stops_at_the_last(env):
    cat, pf, ld, view = env
    view.set_advance_on_mark(True)
    view.open([0, 1, 2], focus=0)
    key(view, Qt.Key.Key_Space)
    assert view.focused_index() == 1
    key(view, Qt.Key.Key_Space)
    key(view, Qt.Key.Key_Space)
    assert view.focused_index() == 2  # stays on the last
    assert cat.is_marked(0) and cat.is_marked(1) and cat.is_marked(2)
    key(view, Qt.Key.Key_Space)  # unmarks the last, still there
    assert view.focused_index() == 2 and not cat.is_marked(2)


def test_n_grows_then_closes_for_a_run(env):
    cat, pf, ld, view = env
    seen = []
    view.closed.connect(seen.append)
    view.open([2, 3], focus=1, run=True)
    key(view, Qt.Key.Key_N)
    assert view.indices() == [2, 3, 4]
    key(view, Qt.Key.Key_N)
    assert view.indices() == [2, 3, 4, 5] and seen == []
    key(view, Qt.Key.Key_N)
    assert seen == [3] and view.indices() == []


def test_n_closes_at_once_for_a_grid_pick(env):
    cat, pf, ld, view = env
    seen = []
    view.closed.connect(seen.append)
    view.open([0, 3], focus=0, run=False)
    key(view, Qt.Key.Key_N)
    assert seen == [0] and view.indices() == []


def test_n_at_the_end_of_the_catalog_closes(env):
    cat, pf, ld, view = env
    seen = []
    view.closed.connect(seen.append)
    view.open([N - 2, N - 1], run=True)
    key(view, Qt.Key.Key_N)
    assert seen == [N - 2]


def test_esc_closes_and_emits_focused_index(env):
    cat, pf, ld, view = env
    seen = []
    view.closed.connect(seen.append)
    view.open([1, 2], focus=1)
    key(view, Qt.Key.Key_Escape)
    assert seen == [2]
    view.close_compare()  # already closed: no second signal
    assert seen == [2]


def test_uncached_cell_decodes_on_the_pool(env):
    cat, pf, ld, view = env
    view.open([6, 7])
    pump(lambda: all(view.cell(i).fit_img is not None for i in (6, 7)))
    assert sorted(ld.calls) == [(6, 2560), (7, 2560)]
    assert colour_at(view.cell(6)) == colour(6) and colour_at(view.cell(7)) == colour(7)


def test_failed_decode_shows_a_message(env):
    cat, pf, ld, view = env
    ld.fail.add(6)
    view.open([6, 0])
    pump(lambda: "bad picture" in view.cell(6).message.text())
    assert view.cell(6).fit_img is None and view.cell(0).fit_img is not None


def test_stale_decode_is_dropped_and_never_lands_in_another_cell(env):
    cat, pf, ld, view = env
    ld.gates[(6, 2560)] = gate6 = threading.Event()
    view.open([6])
    pump(lambda: (6, 2560) in ld.calls)
    view.open([7])  # a new set while 6 is still decoding
    pump(lambda: view.cell(7).fit_img is not None)
    gate6.set()
    assert view.wait_idle()
    QTest.qWait(30)
    assert view.indices() == [7] and view.cell(6) is None
    assert colour_at(view.cell(7)) == colour(7)


def test_stale_result_after_close_is_dropped(env):
    cat, pf, ld, view = env
    ld.gates[(6, 2560)] = gate = threading.Event()
    view.open([6, 0])
    pump(lambda: (6, 2560) in ld.calls)
    view.close_compare()
    gate.set()
    assert view.wait_idle()
    QTest.qWait(30)
    assert view.indices() == [] and view._pending == set()


def test_zoom_decodes_full_size_and_locks_pan(env):
    cat, pf, ld, view = env
    view.open([1, 2, 3], focus=0)
    key(view, Qt.Key.Key_Z)
    assert view.is_zoomed()
    pump(lambda: all(view.cell(i).zoom_img is not None for i in (1, 2)))
    assert view.wait_idle()
    assert sorted(c for c in ld.calls if c[1] is None) == [(1, None), (2, None)]  # two cells at 100%, not three
    assert view.cell(3).zoom_img is None and not view.cell(3).zoomed
    assert view.cell(2).zoom_img.width() == 1200 and view.cell(1).zoom_img.width() == 1600
    assert view.cell(1).caption.text() == "preview 100%" and not view.cell(1).caption.isHidden()
    QTest.qWait(30)
    for i in (1, 2):  # both start at the same fractional centre
        fx, fy = view.cell(i).centre_fraction()
        assert abs(fx - 0.5) < 0.02 and abs(fy - 0.5) < 0.02
    c1 = view.cell(1)
    c1.horizontalScrollBar().setValue(c1.horizontalScrollBar().value() + 120)
    c1.verticalScrollBar().setValue(c1.verticalScrollBar().value() - 60)
    ref = c1.centre_fraction()
    assert abs(ref[0] - 0.5) > 0.02
    fx, fy = view.cell(2).centre_fraction()  # the other follows by fraction, portrait included
    assert abs(fx - ref[0]) < 0.02 and abs(fy - ref[1]) < 0.02
    view.cell(2).horizontalScrollBar().setValue(view.cell(2).horizontalScrollBar().minimum())
    ref = view.cell(2).centre_fraction()
    assert abs(view.cell(1).centre_fraction()[0] - ref[0]) < 0.02


def test_zoom_caps_at_two_cells_around_the_focus(env):
    cat, pf, ld, view = env
    view.open([0, 1, 2, 3], focus=3)
    key(view, Qt.Key.Key_Z)
    pump(lambda: all(view.cell(i).zoom_img is not None for i in (2, 3)))
    assert view.wait_idle()
    assert sorted(c for c in ld.calls if c[1] is None) == [(2, None), (3, None)]
    key(view, Qt.Key.Key_Z)
    assert all(view.cell(i).zoom_img is None and not view.cell(i).zoomed for i in range(4))


def test_zoom_shows_fitted_upscaled_until_the_decode_lands(env):
    cat, pf, ld, view = env
    ld.gates[(1, None)] = gate = threading.Event()
    view.open([1])
    key(view, Qt.Key.Key_Z)
    pump(lambda: (1, None) in ld.calls)
    cell = view.cell(1)
    assert cell.zoom_img is None and cell.has_image and cell.caption.text() == "decoding…"
    gate.set()
    pump(lambda: cell.zoom_img is not None)
    key(view, Qt.Key.Key_Z)  # back to fit frees the full-size image
    assert not view.is_zoomed() and cell.zoom_img is None and cell.caption.isHidden()


def test_unzoom_drops_pending_full_results(env):
    cat, pf, ld, view = env
    ld.gates[(0, None)] = gate = threading.Event()
    view.open([0])
    key(view, Qt.Key.Key_Z)
    pump(lambda: (0, None) in ld.calls)
    key(view, Qt.Key.Key_Z)
    gate.set()
    assert view.wait_idle()
    QTest.qWait(30)
    assert view.cell(0).zoom_img is None and not view.is_zoomed()


def test_close_frees_zoomed_images(env):
    cat, pf, ld, view = env
    view.open([0, 1])
    key(view, Qt.Key.Key_Z)
    pump(lambda: all(view.cell(i).zoom_img is not None for i in (0, 1)))
    cells = [view.cell(0), view.cell(1)]
    seen = []
    view.closed.connect(seen.append)
    view.close_compare()
    assert seen == [0] and not view.is_zoomed() and all(c.zoom_img is None for c in cells)


def test_overlay_scales_over_the_fitted_cell_only(env):
    cat, pf, ld, view = env
    view.open([0, 1])
    mask = QImage(100, 75, QImage.Format.Format_ARGB32)
    mask.fill(0x80FF0000)
    view.set_overlay(0, mask)
    c0 = view.cell(0)
    assert c0.overlay.isVisible() and not view.cell(1).overlay.isVisible()
    t = c0.overlay.transform()
    assert abs(t.m11() - 4.0) < 1e-6 and abs(t.m22() - 4.0) < 1e-6  # 100x75 over a 400x300 fitted image
    view.set_overlay(0, None)
    assert not c0.overlay.isVisible()
    view.set_overlay(0, mask)
    key(view, Qt.Key.Key_Z)
    assert not c0.overlay.isVisible()


def test_status_text(env):
    cat, pf, ld, view = env
    cat.set_mark(1, True)
    cat.set_mark(7, True)
    view.open([0, 1, 2])
    assert view.status_text() == "compare 3 · 1 marked"
