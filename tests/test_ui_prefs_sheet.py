"""Preferences sheet and its wiring into MainWindow, offscreen. The sheet is never exec()'d."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QSpinBox

from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui import settings
from spuza.ui.prefs_sheet import PrefsSheet
from spuza.ui.window import MainWindow

APP = QApplication.instance() or QApplication([])

ROOT = Path("/cards/X-PRO2")
PICS = [Picture(f"A{i}", ROOT / f"A{i}.JPG", None, (ROOT / f"A{i}.JPG",), "JPG", 1000, 1.0) for i in range(4)]


def fake_loader(path, max_side):
    return Image.new("RGB", (64, 48), (200, 80, 40)), PreviewInfo(path, "raf-embedded", 1920, 1280, 1000, 3.5)


class RecordingPrefetcher(Prefetcher):
    """Stands in for the sibling's Prefetcher.set_window: records the calls."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.window_calls = []

    def set_window(self, ahead, behind):
        self.window_calls.append((ahead, behind))
        self.ahead, self.behind = ahead, behind


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond(), "condition not reached in time"


@pytest.fixture
def win():
    cat = Catalog(PICS)
    pf = RecordingPrefetcher(cat, loader=fake_loader, ahead=5, behind=3)
    w = MainWindow(cat, pf, ROOT)
    w.resize(1200, 800)
    w.show()
    pump(lambda: w.viewer.has_image)
    yield w
    w.close()


def stored_window():
    return settings.read_window(settings.open_settings())


def spin_labels(sheet):
    return sorted(t.text() for t in sheet.findChildren(QLabel) if t.text() in ("Pictures ready ahead", "Pictures kept behind"))


def test_sheet_labels_ranges_and_initial_values():
    sheet = PrefsSheet(5, 3, 2560)
    spins = sheet.findChildren(QSpinBox)
    assert len(spins) == 2
    assert spin_labels(sheet) == ["Pictures kept behind", "Pictures ready ahead"]
    assert (sheet.ahead_spin.minimum(), sheet.ahead_spin.maximum()) == (1, 64)
    assert (sheet.behind_spin.minimum(), sheet.behind_spin.maximum()) == (0, 64)
    assert sheet.values() == (5, 3)


def test_memory_line_updates_live():
    sheet = PrefsSheet(12, 8, 2560)
    assert sheet.memory_text() == f"about {settings.window_mb(12, 8, 2560)} MB of memory"
    sheet.ahead_spin.setValue(30)
    assert sheet.memory_text() == f"about {settings.window_mb(30, 8, 2560)} MB of memory"
    sheet.behind_spin.setValue(0)
    assert sheet.memory_text() == f"about {settings.window_mb(30, 0, 2560)} MB of memory"
    small = PrefsSheet(12, 8, 1280)
    assert small.memory_text() == f"about {settings.window_mb(12, 8, 1280)} MB of memory"


def test_reset_to_defaults_sets_spins_only():
    sheet = PrefsSheet(40, 0, 2560)
    sheet.reset_button.click()
    assert sheet.values() == (settings.DEFAULT_AHEAD, settings.DEFAULT_BEHIND) == (12, 8)
    assert sheet.memory_text() == "about 275 MB of memory"
    assert stored_window() == (12, 8)  # defaults either way; nothing was written
    assert not settings.open_settings().contains(settings.PREFETCH_AHEAD)


def test_ok_writes_store_and_applies_live(win):
    seen = []

    def factory(ahead, behind, max_side, parent=None):
        seen.append((ahead, behind, max_side))
        return PrefsSheet(ahead, behind, max_side, parent=parent)

    win.prefs_sheet_factory = factory
    win.open_prefs()
    assert seen == [(5, 3, win.prefetcher.max_side)]  # the running window, not the store (12/8)
    sheet = win._prefs_sheet
    assert sheet.values() == (5, 3)
    sheet.ahead_spin.setValue(20)
    sheet.behind_spin.setValue(2)
    sheet.accept()
    assert win.prefetcher.window_calls == [(20, 2)]
    assert stored_window() == (20, 2)
    assert win.last_message.startswith("Prefetch: 20 ahead, 2 behind (about ")
    assert f"{settings.window_mb(20, 2, win.prefetcher.max_side)} MB" in win.last_message


def test_cancel_changes_nothing(win):
    win.open_prefs()
    sheet = win._prefs_sheet
    sheet.ahead_spin.setValue(30)
    sheet.reject()
    assert win.prefetcher.window_calls == []
    assert not settings.open_settings().contains(settings.PREFETCH_AHEAD)
    assert (win.prefetcher.ahead, win.prefetcher.behind) == (5, 3)


def test_ctrl_comma_opens_the_sheet(win):
    assert win._prefs_sheet is None
    QTest.keyClick(win, Qt.Key_Comma, Qt.ControlModifier)
    assert isinstance(win._prefs_sheet, PrefsSheet) and win._prefs_sheet.isVisible()
    win._prefs_sheet.reject()


def test_toolbar_button_has_its_cap_and_opens_the_sheet(win):
    b = win.prefs_button
    assert b.cap == ("⌘," if sys.platform == "darwin" else "Ctrl+,")
    assert b.label == "Preferences…"
    # on the toolbar, left of copy (copy stays rightmost); not asserting isVisible: under the test style the
    # toolbar is wider than the window and even copy folds into the overflow chevron
    order = [win.toolbar.widgetForAction(a) for a in win.toolbar.actions()]
    assert b in order and order.index(b) < order.index(win.buttons["copy"]) == len(order) - 1
    b.click()
    assert isinstance(win._prefs_sheet, PrefsSheet)
    win._prefs_sheet.reject()


def test_opens_in_review_mode_too(win):
    win.review_factory = lambda catalog, prefetcher=None, parent=None: _fake_review(catalog, parent)
    win.toggle_review()
    assert win.stack.currentIndex() == 1
    QTest.keyClick(win, Qt.Key_Comma, Qt.ControlModifier)
    assert win._prefs_sheet is not None and win._prefs_sheet.isVisible()
    win._prefs_sheet.reject()


def _fake_review(catalog, parent):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QWidget

    class R(QWidget):
        open_index = Signal(int)
        marks_changed = Signal()
        closed = Signal()

        def refresh(self):
            pass

    return R(parent)
