"""ReviewGrid: marked pictures only, unmark, zoom, keys, percentage, thumbnails (offscreen)."""

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza.catalog import Catalog, Picture
from spuza.ui.review import PAGE_EMPTY, PAGE_GRID, PAGE_ZOOM, ReviewGrid, header_text, percent


# Widgets need a QApplication, and test_catalog.py's session fixture makes a bare QGuiApplication that
# cannot be upgraded later (the process then dies in the first widget). Creating the QApplication at
# collection time, before any fixture runs, is the only way to be order-proof without touching
# conftest.py; a QApplication still serves the QGuiApplication tests. Move into conftest when owned.
_APP = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def qapp():
    return _APP


def pic(n: int, kind="RAF+JPG", size=1_000_000) -> Picture:
    p = Path(f"/nope/R{n:07d}.RAF")
    return Picture(p.stem, p, None, (p,), kind, size, float(n))


def catalog(n=10, marked=(1, 3, 5)):
    kinds = ["RAF+JPG", "DNG only", "JPG"]
    c = Catalog([pic(i, kinds[i % 3]) for i in range(n)])
    for i in marked:
        c.set_mark(i, True)
    return c


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        QTest.qWait(5)
    return cond()


def fake_loader_factory(calls):
    def loader(path, max_side):
        calls.append((Path(path).name, max_side))
        return Image.new("RGB", (40, 30), (200, 10, 10)), None

    return loader


class FakePrefetcher:
    def __init__(self, cached):
        self._c = cached

    def cached(self, i):
        return self._c.get(i)


def solid(color="blue") -> QImage:
    img = QImage(64, 48, QImage.Format.Format_RGB888)
    img.fill(QColor(color))
    return img


def make(qapp, cat=None, prefetcher=None, loader=None):
    calls = []
    g = ReviewGrid(cat or catalog(), prefetcher, loader=loader or fake_loader_factory(calls))
    g.resize(900, 600)
    g.show()
    g._calls = calls
    return g


def test_percent_rounds_down():
    assert percent(19, 37) == 51  # the 02-bench mockup showed 52
    assert percent(1, 3) == 33
    assert percent(2, 3) == 66
    assert percent(37, 37) == 100
    assert percent(0, 37) == 0
    assert percent(5, 0) == 0


def test_header_text():
    assert header_text(19, 418, 4_600_000_000) == "19 marked of 418 · 4.6 GB"


def test_grid_shows_only_marked(qapp):
    g = make(qapp)
    assert g.model.rowCount() == 3
    assert [g.model.item(r).data(Qt.ItemDataRole.UserRole + 1) for r in range(3)] == [1, 3, 5]
    assert g.header.text() == "3 marked of 10 · 3 MB"
    assert g.stack.currentIndex() == PAGE_GRID
    assert g.model.item(0).data(Qt.ItemDataRole.UserRole + 2) == "DNG only"
    g.close()


def test_empty_state(qapp):
    g = make(qapp, catalog(marked=()))
    assert g.stack.currentIndex() == PAGE_EMPTY
    assert g.empty.text() == "Nothing marked yet"
    assert not g.unmark_button.isEnabled()
    g.close()


def test_unmark_updates_catalog_and_emits(qapp):
    cat = catalog()
    g = make(qapp, cat)
    hits = []
    g.marks_changed.connect(lambda: hits.append(1))
    g.view.setCurrentIndex(g.model.index(1, 0))  # catalog index 3
    QTest.keyClick(g.view, Qt.Key.Key_M)
    assert not cat.is_marked(3) and cat.is_marked(1) and cat.is_marked(5)
    assert g.model.rowCount() == 2
    assert hits == [1]
    assert g.selected_index() == 5  # selection stays at the same place
    g.unmark_button.click()
    assert not cat.is_marked(5) and hits == [1, 1]
    g.close()


def test_space_unmarks_delete_does_not(qapp):
    cat = catalog()
    g = make(qapp, cat)
    hits = []
    g.marks_changed.connect(lambda: hits.append(1))
    QTest.keyClick(g.view, Qt.Key.Key_Delete)
    QTest.keyClick(g, Qt.Key.Key_Backspace)
    assert cat.marked_count == 3 and hits == []
    QTest.keyClick(g.view, Qt.Key.Key_Space)
    assert cat.marked_count == 2 and hits == [1]
    g.close()


def test_unmark_last_shows_empty(qapp):
    cat = catalog(marked=(2,))
    g = make(qapp, cat)
    g.unmark_button.click()
    assert g.stack.currentIndex() == PAGE_EMPTY
    assert g.header.text().startswith("0 marked of 10")
    g.close()


def test_enter_emits_catalog_index(qapp):
    g = make(qapp)
    got = []
    g.open_index.connect(got.append)
    g.view.setCurrentIndex(g.model.index(2, 0))
    QTest.keyClick(g.view, Qt.Key.Key_Return)
    QTest.keyClick(g, Qt.Key.Key_Enter)
    assert got == [5, 5]
    g.close()


def test_zoom_toggle_and_esc(qapp):
    g = make(qapp)
    closed = []
    g.closed.connect(lambda: closed.append(1))
    g.view.setCurrentIndex(g.model.index(0, 0))
    QTest.keyClick(g.view, Qt.Key.Key_Z)
    assert g.stack.currentIndex() == PAGE_ZOOM
    assert wait_for(lambda: g._zoom_item is not None)
    assert g._zoom_fit
    QTest.keyClick(g.zoom_view, Qt.Key.Key_Z)  # toggles 100%, stays on the zoom page
    assert g.stack.currentIndex() == PAGE_ZOOM and not g._zoom_fit
    assert g.zoom_view.transform().m11() == 1.0
    QTest.keyClick(g.zoom_view, Qt.Key.Key_Z)
    assert g._zoom_fit
    QTest.keyClick(g.zoom_view, Qt.Key.Key_Escape)  # back to the grid, not closed
    assert g.stack.currentIndex() == PAGE_GRID and closed == []
    QTest.keyClick(g.view, Qt.Key.Key_Escape)
    assert closed == [1]
    g.wait_idle()
    g.close()


def test_double_click_zooms_and_unmark_from_zoom(qapp):
    cat = catalog()
    g = make(qapp, cat)
    idx = g.model.index(0, 0)
    g.view.setCurrentIndex(idx)
    g.view.doubleClicked.emit(idx)
    assert g.stack.currentIndex() == PAGE_ZOOM
    g.unmark_selected()
    assert not cat.is_marked(1)
    assert g.stack.currentIndex() == PAGE_GRID
    g.wait_idle()
    g.close()


def test_thumbnails_from_prefetcher_when_cached(qapp):
    pf = FakePrefetcher({1: solid("red"), 3: solid("green"), 5: solid("blue")})
    calls = []
    g = make(qapp, prefetcher=pf, loader=fake_loader_factory(calls))
    assert all(g._pixmap(i) is not None for i in (1, 3, 5))
    assert g.wait_idle() and calls == []  # nothing decoded by the grid
    g.close()


def test_thumbnails_from_worker_otherwise(qapp):
    pf = FakePrefetcher({1: solid("red")})  # 3 and 5 are not cached
    calls = []
    g = make(qapp, prefetcher=pf, loader=fake_loader_factory(calls))
    assert wait_for(lambda: g._pixmap(3) is not None and g._pixmap(5) is not None)
    assert sorted(n for n, _ in calls) == ["R0000003.RAF", "R0000005.RAF"]
    assert all(side == 480 for _, side in calls)
    g.close()


def test_failed_decode_keeps_placeholder(qapp):
    def bad(path, max_side):
        raise RuntimeError("boom")

    g = make(qapp, loader=bad)
    assert wait_for(lambda: len(g._failed) == 3)
    assert g._pixmap(1) is None and not g._inflight
    g.close()


def test_inflight_is_bounded(qapp, monkeypatch):
    import spuza.ui.review as rv

    monkeypatch.setattr(rv, "MAX_INFLIGHT", 2)
    seen = []

    g = None

    def loader(path, max_side):
        seen.append(len(g._inflight))
        return Image.new("RGB", (10, 10)), None

    cat = catalog(n=12, marked=tuple(range(12)))
    g = ReviewGrid(cat, None, loader=loader)
    assert wait_for(lambda: len(g._thumbs) == 12)
    assert max(seen) <= 2
    g.close()
