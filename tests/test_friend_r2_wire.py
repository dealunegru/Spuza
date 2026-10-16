"""Friend round 2 wiring: the three copy rows in Preferences, the copy sheet's options and eject target,
its quit request, and the card mount cli.main hands the window. No real eject, no real copy."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza import cli
from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui import settings
from spuza.ui.window import MainWindow

APP = QApplication.instance() or QApplication([])

ROOT = Path("/cards/X-PRO2")
PICS = [Picture(f"A{i}", ROOT / f"A{i}.JPG", None, (ROOT / f"A{i}.JPG",), "JPG", 1000, 1.0) for i in range(3)]
KEYS = ("copy/organise", "copy/eject", "copy/quit")


def fake_loader(path, max_side):
    return Image.new("RGB", (64, 48), (200, 80, 40)), PreviewInfo(path, "raf-embedded", 1920, 1280, 1000, 3.5)


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond(), "condition not reached in time"


def make_window(**kw):
    cat = Catalog(PICS)
    w = MainWindow(cat, Prefetcher(cat, loader=fake_loader, ahead=2, behind=1), ROOT, **kw)
    w.resize(1000, 700)
    w.show()
    pump(lambda: w.viewer.has_image)
    return w


@pytest.fixture
def win():
    w = make_window()
    yield w
    w.close()


def stored_keys():
    s = settings.open_settings()
    s.sync()
    return {k for k in s.allKeys() if k.startswith("copy/")}


def open_sheet(win):
    win.open_prefs()
    return win._prefs_sheet


def test_prefs_factory_seeds_from_saved_values(win):
    settings.write_organise(win.prefs, False)
    settings.write_eject_after(win.prefs, False)
    settings.write_quit_after(win.prefs, True)
    sheet = win._default_prefs_sheet_factory(5, 3, 2560)
    assert (sheet.organise(), sheet.eject_after(), sheet.quit_after()) == (False, False, True)
    assert (sheet.seeded_organise, sheet.seeded_eject, sheet.seeded_quit) == (False, False, True)


def test_ok_without_change_writes_no_copy_keys(win):
    open_sheet(win).accept()
    assert stored_keys() == set()


def test_flipping_one_row_writes_only_that_key(win):
    sheet = open_sheet(win)
    sheet.eject_check.setChecked(False)
    sheet.accept()
    assert stored_keys() == {"copy/eject"}
    s = settings.open_settings()
    assert settings.read_eject_after(s) is False
    assert settings.read_organise(s) is True


def test_copy_rows_save_in_classic_mode(win):
    from spuza import features

    assert not features.is_advanced()
    sheet = open_sheet(win)
    sheet.organise_check.setChecked(False)
    sheet.accept()
    assert settings.read_organise(settings.open_settings()) is False


def test_copy_factory_passes_saved_values_and_card_mount(tmp_path):
    w = make_window(card_mount=tmp_path)
    try:
        assert w.card_mount == tmp_path
        settings.write_organise(w.prefs, False)
        settings.write_eject_after(w.prefs, False)
        settings.write_quit_after(w.prefs, False)
        sheet = w._default_copy_sheet_factory(PICS)
        assert (sheet.organise, sheet.eject_after, sheet.quit_after) == (False, False, False)
        assert sheet.eject_target == tmp_path
        settings.write_quit_after(w.prefs, True)  # read each time the sheet opens
        assert w._default_copy_sheet_factory(PICS).quit_after is True
    finally:
        w.close()


def test_card_mount_defaults_to_none(win):
    assert win.card_mount is None
    assert win._default_copy_sheet_factory(PICS).eject_target is None


def test_quit_requested_closes_the_window(win):
    sheets = []

    def factory(pictures, default_dest=None, parent=None):
        s = win._default_copy_sheet_factory(pictures, default_dest=default_dest, parent=parent)
        s.open = lambda: None  # never shown, never copies
        sheets.append(s)
        return s

    win.copy_sheet_factory = factory
    win.catalog.marked = lambda: list(PICS)
    win.open_copy()
    assert sheets
    sheets[0].quit_requested.emit()
    assert not win.isVisible()


def test_open_copy_tolerates_a_sheet_without_quit_requested(win):
    class Fake:
        def open(self):
            pass

    win.catalog.marked = lambda: list(PICS)
    win.copy_sheet_factory = lambda pictures, default_dest=None, parent=None: Fake()
    win.open_copy()
    assert win.isVisible()


def test_card_mount_for_a_card_run(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    run = cli.CardRun(b / "DCIM", (a, b), 1)
    assert cli.card_mount_for(run.root, [a, b], run) == b


def test_card_mount_for_paths(tmp_path):
    card = tmp_path / "card"
    (card / "DCIM" / "100").mkdir(parents=True)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert cli.card_mount_for(card / "DCIM" / "100", [card]) == card
    assert cli.card_mount_for(card, [card]) == card
    assert cli.card_mount_for(plain, [card]) is None
    assert cli.card_mount_for(plain, []) is None
    assert cli.card_mount_for(tmp_path / "card2", [card]) is None  # a sibling prefix is not a child
