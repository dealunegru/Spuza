"""Bugfinder regressions for CopySheet: B10 space check, B11 second folder, B12 close, B13 result line."""

import threading
import time

from PySide6.QtWidgets import QApplication

from spuza import copy as copy_mod
from spuza.ui.copy_sheet import CopySheet
import pytest

from test_ui_copy_sheet import make_card, wait_for  # noqa: F401  (also makes the QApplication)


@pytest.fixture(autouse=True)
def _logs_dir(tmp_path, monkeypatch):
    """Copy logs go to a per-test folder, never the real app-data one (and never the destination)."""
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "spuza-logs"))


def test_b10_everything_already_there_is_not_short_of_space(tmp_path):
    pics = make_card(tmp_path)
    out = tmp_path / "out"
    first = CopySheet(pics, out)
    done = []
    first.finished.connect(done.append)
    first.copy_button.click()
    assert wait_for(lambda: done)
    first.wait_idle()
    sheet = CopySheet(pics, out)
    sheet.free_space_fn = lambda dest: 0
    sheet._update_dest()
    assert sheet.copy_button.isEnabled()
    assert "Not enough space" not in sheet.free_label.text()
    assert "skip 5 already there" in sheet.plan_label.text()


def test_b11_second_folder_after_finished_copy_has_a_copy_button(tmp_path):
    sheet = CopySheet(make_card(tmp_path), tmp_path / "out", organise=False)
    done = []
    sheet.finished.connect(done.append)
    sheet.copy_button.click()
    assert wait_for(lambda: done)
    sheet.wait_idle()
    sheet.show()
    sheet.choose_folder = lambda *a: str(tmp_path / "second")
    sheet.choose_button.click()
    assert sheet.dest == tmp_path / "second"
    assert sheet.copy_button.isEnabled() and not sheet.copy_button.isHidden()
    done.clear()
    sheet.copy_button.click()
    assert wait_for(lambda: done)
    sheet.wait_idle()
    assert len(done[0].copied) == 5 and (tmp_path / "second" / "R0000.DNG").is_file()


def test_b12_close_mid_copy_does_not_block_and_finishes_once(tmp_path, monkeypatch):
    release = threading.Event()

    def slow(plan, progress=None, cancel=None, log_path=None):
        end = time.monotonic() + 5
        while time.monotonic() < end and not cancel():
            release.wait(0.01)
        release.wait(1.5)  # winding down after cancel takes a while (a big chunk, a slow card)
        return copy_mod.CopyResult(cancelled=True)

    monkeypatch.setattr(copy_mod, "run_copy", slow)
    sheet = CopySheet(make_card(tmp_path), tmp_path / "out")
    sheet.show()
    sheet.confirm_close = lambda: True
    results = []
    sheet.finished.connect(results.append)
    sheet.copy_button.click()
    t0 = time.monotonic()
    sheet.close()
    assert time.monotonic() - t0 < 1.0
    assert not sheet.isVisible()
    assert wait_for(lambda: results)
    assert sheet.wait_idle()
    for _ in range(5):
        QApplication.processEvents()
    assert len(results) == 1 and results[0].cancelled
    assert not sheet.running and not sheet.isVisible()


def _picture_result(pics):
    pair = next(p for p in pics if len(p.files) == 2)
    raf = next(f for f in pair.files if f.suffix == ".RAF")
    jpg = next(f for f in pair.files if f.suffix == ".JPG")
    return pair, copy_mod.CopyResult(copied=[raf], failed=[(jpg, "disk full")])


def test_b13_partly_copied_picture_is_not_counted_copied(tmp_path):
    pics = make_card(tmp_path, n=0)
    sheet = CopySheet(pics, tmp_path / "out")
    _, res = _picture_result(pics)
    sheet._on_done(res)
    text = sheet.result_label.text()
    assert text.startswith("Copied 0 pictures")
    assert "failed 1" in text and "1 picture not fully copied" in text
