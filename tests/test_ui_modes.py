"""Classic/advanced mode in the UI: the Preferences checkbox, the advanced-only threads row, the ADVANCED marker."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from spuza import features
from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui import settings
from spuza.ui.prefs_sheet import PrefsSheet
from spuza.ui.window import MainWindow

APP = QApplication.instance() or QApplication([])

ROOT = Path("/cards/X-PRO2")
PICS = [Picture(f"A{i}", ROOT / f"A{i}.JPG", None, (ROOT / f"A{i}.JPG",), "JPG", 1000, 1.0) for i in range(3)]


def fake_loader(path, max_side):
    return Image.new("RGB", (64, 48), (200, 80, 40)), PreviewInfo(path, "raf-embedded", 1920, 1280, 1000, 3.5)


@pytest.fixture(autouse=True)
def classic_after():
    features.set_mode(features.CLASSIC)
    yield
    features.set_mode(features.CLASSIC)


@pytest.fixture
def make_win():
    made = []

    def make():
        cat = Catalog(PICS)
        w = MainWindow(cat, Prefetcher(cat, loader=fake_loader, ahead=5, behind=3), ROOT)
        w.resize(1000, 700)
        w.show()
        made.append(w)
        return w

    yield make
    for w in made:
        w.close()


def stored():
    return settings.open_settings()


def test_classic_window_is_unchanged(make_win):
    w = make_win()
    assert w.windowTitle() == f"Spuza — {Path(ROOT)}"
    assert not w.advanced_tag.isVisible()


def test_advanced_window_title_and_tag(make_win):
    features.set_mode(features.ADVANCED)
    w = make_win()
    assert w.windowTitle() == f"Spuza (advanced) — {Path(ROOT)}"
    assert w.advanced_tag.isVisible() and w.advanced_tag.text() == "ADVANCED"


def test_set_advanced_is_live_and_sets_the_mode(make_win):
    w = make_win()
    w.set_advanced(True)
    assert features.is_advanced() and w.advanced_tag.isVisible() and "(advanced)" in w.windowTitle()
    w.set_advanced(False)
    assert not features.is_advanced() and not w.advanced_tag.isVisible()
    assert w.windowTitle() == f"Spuza — {Path(ROOT)}"


def test_default_factory_sheet_starts_from_this_runs_mode_and_saved_threads(make_win):
    s = stored()
    settings.write_threads(s, 5)
    s.sync()
    features.set_mode(features.ADVANCED)  # a one-run --advanced: nothing saved as advanced
    w = make_win()
    w.open_prefs()
    sheet = w._prefs_sheet
    assert sheet.advanced() and sheet.threads() == 5
    sheet.reject()


def test_accepting_with_the_box_checked_persists_and_shows_the_marker(make_win):
    w = make_win()
    w.open_prefs()
    sheet = w._prefs_sheet
    assert not sheet.advanced()
    sheet.advanced_check.setChecked(True)
    sheet.threads_spin.setValue(6)
    sheet.accept()
    s = stored()
    assert settings.read_advanced(s) is True
    assert s.value(settings.PREFETCH_THREADS) is not None and settings.read_threads(s) == 6
    assert features.is_advanced() and w.advanced_tag.isVisible()
    assert "(advanced)" in w.windowTitle()


def test_unchecking_hides_the_marker_and_keeps_the_saved_threads_untouched(make_win):
    s = stored()
    settings.write_advanced(s, True)
    settings.write_threads(s, 4)
    s.sync()
    features.set_mode(features.ADVANCED)
    w = make_win()
    w.open_prefs()
    sheet = w._prefs_sheet
    sheet.advanced_check.setChecked(False)
    sheet.accept()
    s = stored()
    assert settings.read_advanced(s) is False and settings.read_threads(s) == 4
    assert not features.is_advanced() and not w.advanced_tag.isVisible()
    assert w.windowTitle() == f"Spuza — {Path(ROOT)}"


def test_accepting_in_classic_writes_no_threads_key(make_win):
    w = make_win()
    w.open_prefs()
    w._prefs_sheet.accept()
    s = stored()
    assert not s.contains(settings.PREFETCH_THREADS)
    assert settings.read_advanced(s) is False
    assert not features.is_advanced()


def test_old_style_fake_sheet_still_applies(make_win):
    class Fake:
        def __init__(self, parent=None):
            from PySide6.QtCore import QObject, Signal

            class Sig(QObject):
                accepted = Signal()

            self._sig = Sig(parent)
            self.accepted = self._sig.accepted

        def values(self):
            return 7, 2

        def show(self):
            pass

    w = make_win()
    w.prefs_sheet_factory = lambda a, b, m, parent=None: Fake(parent)
    w.open_prefs()
    w._prefs_sheet.accepted.emit()
    assert settings.read_window(stored()) == (7, 2)
    assert not features.is_advanced()


def test_threads_row_follows_the_checkbox():
    sheet = PrefsSheet(5, 3, 2560)
    sheet.show()
    assert not sheet.threads_spin.isVisible() and not sheet.threads_label.isVisible()
    assert not sheet.note_label.isVisible()
    sheet.advanced_check.setChecked(True)
    assert sheet.threads_spin.isVisible() and sheet.threads_label.isVisible()
    assert "ADVANCED" in sheet.note_label.text() and "next launch" in sheet.note_label.text()
    assert sheet.note_label.isVisible()
    sheet.advanced_check.setChecked(False)
    assert not sheet.threads_spin.isVisible()
    sheet.close()


def test_sheet_keyword_args_and_range():
    sheet = PrefsSheet(5, 3, 2560, advanced=True, threads=7)
    assert sheet.advanced() and sheet.threads() == 7
    assert (sheet.threads_spin.minimum(), sheet.threads_spin.maximum()) == settings.THREADS_RANGE
    assert sheet.values() == (5, 3)
    assert sheet.advanced_check.text() == "Advanced mode: test features before they reach classic"
    sheet.close()


def test_reset_to_defaults_leaves_the_mode_alone():
    sheet = PrefsSheet(40, 0, 2560, advanced=True, threads=7)
    sheet.reset_to_defaults()
    assert sheet.advanced() and sheet.threads() == 7
    sheet.close()


def test_one_run_advanced_ok_untouched_keeps_the_run_advanced_and_saves_no_mode(make_win):
    features.set_mode(features.ADVANCED)
    w = make_win()
    w.open_prefs()
    sheet = w._prefs_sheet
    sheet.show()
    assert sheet.advanced() and sheet.threads_spin.isVisible()
    sheet.ahead_spin.setValue(9)
    sheet.accept()
    assert not stored().contains(settings.MODE_ADVANCED)
    assert features.is_advanced() and "(advanced)" in w.windowTitle() and w.advanced_tag.isVisible()


def test_one_run_classic_over_saved_advanced_ok_untouched_keeps_saved_and_run(make_win):
    s = stored()
    settings.write_advanced(s, True)
    s.sync()
    w = make_win()  # mode stays classic, as `--classic` would leave it
    w.open_prefs()
    sheet = w._prefs_sheet
    assert not sheet.advanced()
    sheet.accept()
    assert settings.read_advanced(stored()) is True
    assert not features.is_advanced() and not w.advanced_tag.isVisible()
