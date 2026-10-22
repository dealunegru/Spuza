"""Friend round 8, destination: Choose folder starts the copy, a Master folder preference gives the default destination
<master>/MM_DD, and Choose folder opens at the parent the last chosen copy went under. Synthetic files in tmp_path only."""

from __future__ import annotations

import datetime
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui import settings, window as window_mod
from spuza.ui.copy_sheet import CopySheet
from spuza.ui.prefs_sheet import PrefsSheet
from spuza.ui.window import MainWindow
from test_ui_copy_sheet import make_card, wait_for  # noqa: F401  (also makes the QApplication)

APP = QApplication.instance() or QApplication([])
ROOT = Path("/cards/X-PRO2")
PICS = [Picture(f"A{i}", ROOT / f"A{i}.JPG", None, (ROOT / f"A{i}.JPG",), "JPG", 1000, 1.0) for i in range(2)]
OCT22 = datetime.date(2026, 10, 22)
HUGE = 10**15


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "logs"))


@pytest.fixture
def sheets():
    made = []
    yield made
    for s in made:
        s.confirm_close = lambda: True  # never a modal question in a test
        s.wait_idle()
        QApplication.processEvents()  # deliver the worker's result so the sheet is not 'running'
        s.close()


def sheet_for(tmp_path, made, picked, free=HUGE, **kw):
    """A sheet on a small synthetic card whose Choose folder dialog answers `picked` (a list, popped each time)."""
    s = CopySheet(make_card(tmp_path), tmp_path / "default", **kw)
    asked = []
    s.choose_folder = lambda parent, title, start: (asked.append(start), picked.pop(0))[1]
    s.free_space_fn = lambda p: free
    s.asked = asked
    made.append(s)
    return s


# -- 1. choosing a folder starts the copy ------------------------------------------------------------
def test_pick_starts_the_copy_without_pressing_copy(tmp_path, sheets):
    out = tmp_path / "poze" / "10_22"
    s = sheet_for(tmp_path, sheets, [str(out)])
    done = []
    s.finished.connect(done.append)
    s.choose_button.click()
    assert wait_for(lambda: done)
    assert (out / "soc" / "X0001.JPG").is_file() and (out / "raw" / "X0001.RAF").is_file()
    assert not (tmp_path / "default").exists()


def test_cancelled_pick_changes_nothing(tmp_path, sheets):
    s = sheet_for(tmp_path, sheets, [""])
    s.choose_button.click()
    assert not s.running and s.dest == tmp_path / "default" and s.result is None
    assert not (tmp_path / "default").exists()


def test_pick_with_too_little_space_does_not_start(tmp_path, sheets):
    out = tmp_path / "small"
    s = sheet_for(tmp_path, sheets, [str(out)], free=10)
    s.choose_button.click()
    assert not s.running and s.result is None and not out.exists()
    assert "Not enough space" in s.free_label.text() and s.dest == out


def test_a_new_pick_after_a_finished_copy_copies_again(tmp_path, sheets):
    first, second = tmp_path / "one", tmp_path / "two"
    s = sheet_for(tmp_path, sheets, [str(first), str(second)])
    done = []
    s.finished.connect(done.append)
    s.choose_button.click()
    assert wait_for(lambda: len(done) == 1)
    s.choose_button.click()
    assert wait_for(lambda: len(done) == 2)
    assert (second / "raw" / "X0001.RAF").is_file() and (first / "raw" / "X0001.RAF").is_file()


def test_pick_opens_at_start_folder_when_given(tmp_path, sheets):
    s = sheet_for(tmp_path, sheets, [""], start_folder=tmp_path / "poze")
    s.choose_button.click()
    assert s.asked == [str(tmp_path / "poze")]
    (tmp_path / "b").mkdir()
    s2 = sheet_for(tmp_path / "b", sheets, [""])
    s2.choose_button.click()
    assert s2.asked == [str(tmp_path / "b" / "default")]  # no start folder: the destination, as before


def test_chosen_copy_announces_its_folder_and_default_copy_does_not(tmp_path, sheets):
    out = tmp_path / "poze" / "10_22"
    s = sheet_for(tmp_path, sheets, [str(out)])
    got, done = [], []
    s.copied_to_chosen.connect(got.append)
    s.finished.connect(done.append)
    s.choose_button.click()
    assert wait_for(lambda: done)
    assert got == [out]
    (tmp_path / "p").mkdir()
    plain = CopySheet(make_card(tmp_path / "p"), tmp_path / "plain")
    sheets.append(plain)
    got2, done2 = [], []
    plain.copied_to_chosen.connect(got2.append)
    plain.finished.connect(done2.append)
    plain.copy_button.click()
    assert wait_for(lambda: done2) and got2 == []


def test_note_shows_and_a_pick_hides_it(tmp_path, sheets):
    s = sheet_for(tmp_path, sheets, [str(tmp_path / "x")], note="Master folder /m is not available; copying to the usual folder")
    s.show()
    assert s.dest_note_label.isVisible() and "not available" in s.dest_note_label.text()
    s.choose_button.click()
    assert not s.dest_note_label.isVisible()
    (tmp_path / "q").mkdir()
    plain = CopySheet(make_card(tmp_path / "q"), tmp_path / "o")
    sheets.append(plain)
    plain.show()
    assert not plain.dest_note_label.isVisible()


# -- 2. settings -------------------------------------------------------------------------------------
def test_settings_round_trip_and_defaults(tmp_path):
    s = settings.open_settings()
    assert settings.read_master_folder(s) == "" and settings.read_last_parent(s) == ""
    settings.write_master_folder(s, str(tmp_path / "m"))
    settings.write_last_parent(s, str(tmp_path / "m" / "poze"))
    s.sync()
    s2 = settings.open_settings()  # a new store on the same file: survives a restart
    assert settings.read_master_folder(s2) == str(tmp_path / "m")
    assert settings.read_last_parent(s2) == str(tmp_path / "m" / "poze")
    settings.write_master_folder(s2, "")
    assert settings.read_master_folder(s2) == ""


def test_settings_reads_garbage_as_unset():
    s = settings.open_settings()
    s.setValue(settings.MASTER_FOLDER, ["a", "b"])
    assert settings.read_master_folder(s) == ""


# -- window: default destination, note, start folder --------------------------------------------------
def make_window():
    cat = Catalog(PICS)

    def loader(path, max_side):
        return Image.new("RGB", (64, 48)), PreviewInfo(path, "raf-embedded", 64, 48, 1000, 3.5)

    w = MainWindow(cat, Prefetcher(cat, loader=loader, ahead=2, behind=1), ROOT)
    w.resize(1000, 700)
    w.show()
    end = time.monotonic() + 5
    while not w.viewer.has_image and time.monotonic() < end:
        QTest.qWait(5)
    w.today = lambda: OCT22
    return w


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setattr(window_mod, "default_copy_dest", lambda: tmp_path / "usual")
    w = make_window()
    yield w
    w.close()


def open_with(win):
    calls = []

    class Fake:
        def open(self):
            pass

    def factory(pictures, default_dest=None, parent=None, **kw):
        calls.append((default_dest, kw))
        return Fake()

    win.copy_sheet_factory = factory
    win.catalog.marked = lambda: list(PICS)
    win.open_copy()
    return calls


def test_month_day_is_zero_padded():
    assert window_mod.month_day(datetime.date(2026, 3, 5)) == "03_05"
    assert window_mod.month_day(OCT22) == "10_22"


def test_no_master_means_the_usual_folder_and_no_new_kwargs(win, tmp_path):
    assert open_with(win) == [(tmp_path / "usual", {})]  # a factory that knows nothing new is called as before


def test_master_set_defaults_to_master_month_day(win, tmp_path):
    master = tmp_path / "master"
    master.mkdir()
    settings.write_master_folder(win.prefs, str(master))
    [(dest, kw)] = open_with(win)
    assert dest == master / "10_22"
    assert kw == {"start_folder": master}
    assert not (master / "10_22").exists()  # the copy makes it, not the sheet


def test_enter_copies_into_master_month_day(win, tmp_path, sheets):
    master = tmp_path / "master"
    master.mkdir()
    settings.write_master_folder(win.prefs, str(master))
    s = win._default_copy_sheet_factory(make_card(tmp_path), default_dest=win._copy_destination()[0])
    sheets.append(s)
    done = []
    s.finished.connect(done.append)
    s.copy_button.click()
    assert wait_for(lambda: done)
    assert (master / "10_22" / "raw" / "X0001.RAF").is_file()


def test_missing_master_falls_back_with_a_note_and_is_never_created(win, tmp_path):
    master = tmp_path / "unplugged"
    settings.write_master_folder(win.prefs, str(master))
    [(dest, kw)] = open_with(win)
    assert dest == tmp_path / "usual"
    assert "not available" in kw["note"] and str(master) in kw["note"]
    assert "start_folder" not in kw
    assert not master.exists()
    sheet = win._default_copy_sheet_factory(PICS, default_dest=dest, note=kw["note"])
    assert str(master) in sheet.dest_note_label.text()


def test_start_folder_rules(win, tmp_path):
    master, poze, elsewhere = tmp_path / "master", tmp_path / "master" / "poze", tmp_path / "elsewhere"
    for d in (poze, elsewhere):
        d.mkdir(parents=True)
    # no master, nothing remembered: the current destination
    assert win._copy_destination()[2] is None
    # no master, a remembered parent that exists
    settings.write_last_parent(win.prefs, str(elsewhere))
    assert win._copy_destination()[2] == elsewhere
    # ... that no longer exists: back to the destination
    settings.write_last_parent(win.prefs, str(tmp_path / "gone"))
    assert win._copy_destination()[2] is None
    # master set: the last parent when inside the master, else the master
    settings.write_master_folder(win.prefs, str(master))
    settings.write_last_parent(win.prefs, str(poze))
    assert win._copy_destination()[2] == poze
    settings.write_last_parent(win.prefs, str(elsewhere))
    assert win._copy_destination()[2] == master
    assert win._copy_destination()[0] == master / "10_22"


def test_a_chosen_copy_remembers_its_parent_across_a_restart(win, tmp_path, sheets):
    out = tmp_path / "poze" / "10_22"
    s = win._default_copy_sheet_factory(make_card(tmp_path), default_dest=tmp_path / "usual")
    sheets.append(s)
    s.copied_to_chosen.connect(win._on_copied_to_chosen)
    s.choose_folder = lambda *a: str(out)
    s.free_space_fn = lambda p: HUGE
    done = []
    s.finished.connect(done.append)
    s.choose_button.click()
    assert wait_for(lambda: done)
    assert settings.read_last_parent(settings.open_settings()) == str(tmp_path / "poze")


def test_open_copy_connects_the_sheet_signal_to_remember(win, tmp_path):
    class Fake(QObject):
        copied_to_chosen = Signal(object)

        def open(self):
            pass

    fake = Fake()
    win.copy_sheet_factory = lambda pictures, default_dest=None, parent=None, **kw: fake
    win.catalog.marked = lambda: list(PICS)
    win.open_copy()
    fake.copied_to_chosen.emit(tmp_path / "poze" / "10_22")
    assert settings.read_last_parent(win.prefs) == str(tmp_path / "poze")


# -- 3. Preferences row ------------------------------------------------------------------------------
def test_prefs_sheet_master_row(tmp_path):
    p = PrefsSheet(12, 8, master_folder=str(tmp_path / "m"))
    p.show()
    try:
        assert p.master_folder() == p.seeded_master == str(tmp_path / "m")
        assert str(tmp_path / "m") in p.master_label.text()
        assert p.master_choose.isVisible() and p.master_clear.isVisible()
        assert p.master_choose.toolTip() and p.master_clear.toolTip()
        p.choose_folder = lambda parent, title, start: str(tmp_path / "n")
        p.master_choose.click()
        assert p.master_folder() == str(tmp_path / "n")
        p.choose_folder = lambda *a: ""  # cancelled: unchanged
        p.master_choose.click()
        assert p.master_folder() == str(tmp_path / "n")
        p.master_clear.click()
        assert p.master_folder() == "" and not p.master_clear.isEnabled()
        p.reset_to_defaults()
        assert p.master_folder() == ""
    finally:
        p.close()
    assert PrefsSheet(12, 8).master_folder() == ""  # empty by default


def test_apply_prefs_writes_master_only_when_changed(win, tmp_path):
    sheet = win._default_prefs_sheet_factory(12, 8, 2560)
    assert sheet.master_folder() == ""
    win._apply_prefs(sheet)
    assert settings.MASTER_FOLDER not in settings.open_settings().allKeys()
    sheet._master = str(tmp_path / "m")
    win._apply_prefs(sheet)
    assert settings.read_master_folder(settings.open_settings()) == str(tmp_path / "m")
    # a sheet seeded from the saved value and untouched writes nothing: an outside change survives
    again = win._default_prefs_sheet_factory(12, 8, 2560)
    assert again.master_folder() == str(tmp_path / "m")
    settings.write_master_folder(win.prefs, str(tmp_path / "other"))
    win._apply_prefs(again)
    assert settings.read_master_folder(win.prefs) == str(tmp_path / "other")
    again._master = ""
    win._apply_prefs(again)
    assert settings.read_master_folder(win.prefs) == ""
