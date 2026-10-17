"""Filmstrip fr3: far thumbnails load on scroll without a click, the cache is bounded, rotation is painted."""

import time
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from spuza.catalog import Catalog, Picture
from spuza.ui import thumbs
from spuza.ui.filmstrip import CELL_W, Filmstrip


class Empty:
    """A prefetcher that holds nothing: only the strip's own loader can supply thumbnails."""

    def cached(self, i):
        return None


class Holds:
    def __init__(self, img):
        self.img = img

    def cached(self, i):
        return self.img


def make_pics(tmp_path: Path, n: int, size=(64, 32)):
    pics = []
    for i in range(n):
        p = tmp_path / f"P{i:05d}.JPG"
        Image.new("RGB", size, (i % 256, 90, 200 - i % 200)).save(p, "JPEG")
        pics.append(Picture(p.stem, p, None, (p,), "JPG only", p.stat().st_size, float(i)))
    return pics


def pump_until(cond, tries=400):
    for _ in range(tries):  # bounded by count, each pass lets the event loop deliver worker results
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def strip_of(pics, prefetcher):
    s = Filmstrip(Catalog(pics), prefetcher)
    s.resize(600, 120)
    s.show()
    QApplication.processEvents()
    return s


def loader_of(pics):
    return thumbs.ThumbLoader(lambda i: pics[i].primary)


def test_far_scroll_loads_thumbnails_without_a_click(tmp_path):
    pics = make_pics(tmp_path, 120)
    s = strip_of(pics, Empty())
    try:
        bar = s.horizontalScrollBar()
        bar.setValue(bar.maximum())  # scroll to the far end; no click, no set_current
        last = len(pics) - 1
        assert pump_until(lambda: s.thumb(last) is not None)
        assert pump_until(lambda: s.thumb(last - 3) is not None)
        assert s.thumb(0) is None  # nowhere near the view: not requested
    finally:
        s.shutdown()


def test_cache_stays_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbs, "THUMB_CACHE_MAX", 5)
    pics = make_pics(tmp_path, 40)
    loader = loader_of(pics)  # the loader alone: a strip's settle timer would issue its own request
    try:
        loader.request(list(range(20)), 104, 62)
        assert pump_until(lambda: loader._inflight == 0 and not loader._queue)
        assert 0 < len(loader._cache) <= 5
    finally:
        loader.shutdown()


def test_cache_bound_constant_is_at_most_400():
    assert 0 < thumbs.THUMB_CACHE_MAX <= 400
    assert 0 < thumbs.MAX_INFLIGHT <= 8


def test_stale_requests_are_dropped(tmp_path):
    pics = make_pics(tmp_path, 60)
    # the loader alone: through a strip, its 150 ms settle timer fired mid-test on a slow macOS runner and
    # replaced the request with the visible range, so 50..59 were dropped as stale (CI, 2026-10-17)
    loader = loader_of(pics)
    try:
        loader.request(list(range(0, 30)), 104, 62)
        loader.request(list(range(50, 60)), 104, 62)  # the user scrolled on
        assert pump_until(lambda: loader._inflight == 0 and not loader._queue)
        assert all(i in loader._cache for i in range(50, 60))
        assert len(loader._cache) < 30  # most of the first request never decoded
    finally:
        loader.shutdown()


def test_rotated_picture_is_painted_rotated():
    src = QImage(4, 2, QImage.Format.Format_RGB888)
    src.fill(QColor("#c03030"))
    s = Filmstrip(Catalog([Picture("a", Path("/nope/a.jpg"), None, (), "JPG only", 1, 0.0)]), Holds(src))
    s.resize(600, 120)
    flat = s.thumb(0).deviceIndependentSize()
    assert flat.width() == 2 * flat.height()
    s.rotation_of = lambda i: 1
    s.rotation_changed(0)
    turned = s.thumb(0).deviceIndependentSize()
    assert turned.height() == 2 * turned.width()  # 4x2 source -> 2x4 shape at one turn
    s.rotation_of = lambda i: 0
    assert s.thumb(0).deviceIndependentSize() == flat  # the unrotated memo entry is still right


def test_shutdown_returns_within_its_bound(tmp_path):
    pics = make_pics(tmp_path, 50)
    s = strip_of(pics, Empty())
    s.request_visible()
    t0 = time.perf_counter()
    s.shutdown(timeout_ms=3000)
    assert time.perf_counter() - t0 < 3.5
    s.shutdown()  # twice is harmless
    s._loader.request([1, 2], 104, 62)  # closed loader ignores requests


def test_contract_names_exist():
    assert callable(Filmstrip.shutdown) and callable(Filmstrip.rotation_changed)
    assert Filmstrip(Catalog([])).rotation_of(0) == 0


def test_unreadable_file_is_marked_failed_not_retried(tmp_path):
    bad = tmp_path / "bad.JPG"
    bad.write_bytes(b"not a jpeg")
    pics = [Picture("bad", bad, None, (bad,), "JPG only", 10, 0.0)]
    s = strip_of(pics, Empty())
    try:
        loader = s._loader
        loader.request([0], 104, 62)
        assert pump_until(lambda: 0 in loader._failed)
        loader.request([0], 104, 62)
        assert loader._queue == [] and s.thumb(0) is None
    finally:
        s.shutdown()


def test_cell_width_math_matches_layout(tmp_path):
    s = strip_of(make_pics(tmp_path, 100), Empty())
    try:
        s.horizontalScrollBar().setValue(CELL_W * 40)
        QApplication.processEvents()
        lo, hi = s.visible_range()
        assert lo == 40 and hi >= 40 + 600 // CELL_W - 1
        assert s.visualRect(s._model.index(lo)).right() >= 0
    finally:
        s.shutdown()
