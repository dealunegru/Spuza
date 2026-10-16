"""Review grid multi-select exists only in advanced mode with compare_view on; classic is as shipped."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListView

from spuza import features
from spuza.catalog import Catalog, Picture
from spuza.preview import PreviewInfo
from spuza.ui.filmstrip import Filmstrip
from spuza.ui.review import ReviewGrid

ROOT = Path("/cards/X")
NO_MOD, CTRL = Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier


def loader(path, max_side):
    return Image.new("RGB", (64, 48), (90, 120, 200)), PreviewInfo(path, "pillow", 64, 48, 10, 1.0)


def make(marked=(1, 2, 3, 5)):
    pics = [Picture(f"P{i}", ROOT / f"P{i}.JPG", None, (ROOT / f"P{i}.JPG",), "JPG", 1_000_000, 1.0)
            for i in range(7)]
    cat = Catalog(pics)
    for i in marked:
        cat.set_mark(i, True)
    g = ReviewGrid(cat, None, loader=loader)
    g.resize(900, 500)
    g.show()
    QTest.qWait(20)
    return cat, g


def click(g, row, mods):
    pos = g.view.visualRect(g.model.index(row, 0)).center()
    QTest.mouseClick(g.view.viewport(), Qt.MouseButton.LeftButton, mods, pos)


def select_rows(g, rows):
    sm = g.view.selectionModel()
    sm.clearSelection()
    for r in rows:
        sm.select(g.model.index(r, 0), QItemSelectionModel.SelectionFlag.Select)
    sm.setCurrentIndex(g.model.index(rows[-1], 0), QItemSelectionModel.SelectionFlag.NoUpdate)


def test_classic_grid_is_single_selection():
    cat, g = make()
    assert g.view.selectionMode() == QListView.SelectionMode.SingleSelection
    select_rows(g, [0, 2])
    assert g.selected_indices() == [g.selected_index()] and len(g.selected_indices()) == 1
    g.close()


def test_classic_ctrl_click_still_selects_one():
    cat, g = make()
    click(g, 0, NO_MOD)
    click(g, 2, CTRL)
    assert len(g.view.selectionModel().selectedIndexes()) == 1
    g.close()


def test_advanced_grid_is_extended_and_ctrl_click_adds():
    features.set_mode(features.ADVANCED)
    cat, g = make()
    assert g.view.selectionMode() == QListView.SelectionMode.ExtendedSelection
    click(g, 0, NO_MOD)
    click(g, 2, CTRL)
    assert g.selected_indices() == [1, 3]
    assert g.selected_index() == 3  # the current one, for the window
    g.close()


def test_advanced_space_unmarks_every_selected():
    features.set_mode(features.ADVANCED)
    cat, g = make()
    select_rows(g, [0, 1, 3])
    assert g.selected_indices() == [1, 2, 5]
    seen = []
    g.marks_changed.connect(lambda: seen.append(1))
    QTest.keyClick(g.view, Qt.Key.Key_Space)
    assert [i for i in range(7) if cat.is_marked(i)] == [3] and len(seen) == 1
    g.close()


def test_advanced_single_selection_unmarks_one():
    features.set_mode(features.ADVANCED)
    cat, g = make()
    select_rows(g, [1])
    QTest.keyClick(g.view, Qt.Key.Key_M)
    assert not cat.is_marked(2) and cat.is_marked(1) and cat.is_marked(3)
    g.close()


def test_flag_off_after_build_reverts_on_refresh():
    features.set_mode(features.ADVANCED)
    cat, g = make()
    features.set_mode(features.CLASSIC)
    g.refresh()
    assert g.view.selectionMode() == QListView.SelectionMode.SingleSelection
    assert len(g.selected_indices()) == 1
    g.close()


@pytest.fixture
def strip():
    cat = Catalog([Picture(f"P{i}", ROOT / f"P{i}.JPG", None, (ROOT / f"P{i}.JPG",), "JPG", 1, 1.0)
                   for i in range(6)])
    f = Filmstrip(cat, None)
    f.resize(800, 120)
    f.show()
    QTest.qWait(20)
    yield f
    f.close()


def test_filmstrip_highlight_set_and_clear(strip):
    assert strip.highlight == frozenset()
    touched = []
    strip._model.dataChanged.connect(lambda a, b: touched.append(a.row()))
    strip.set_highlight([1, 2])
    assert strip.highlight == frozenset({1, 2}) and sorted(touched) == [1, 2]
    touched.clear()
    strip.set_highlight([2, 3])
    assert sorted(touched) == [1, 3]
    strip.set_highlight([])
    assert strip.highlight == frozenset()


def test_filmstrip_highlight_paints_an_outline(strip):
    r = strip.visualRect(strip._model.index(2))
    before = strip.viewport().grab().toImage()
    strip.set_highlight([2])
    after = strip.viewport().grab().toImage()
    changed = sum(1 for x in range(r.left(), r.right()) for y in range(r.top(), r.bottom())
                  if before.pixel(x, y) != after.pixel(x, y))
    assert changed > 20
