"""Friend round 2: the copy sheet organises, ejects and offers to quit; the Preferences rows (offscreen, no real eject)."""

from pathlib import Path

import pytest

from spuza.ui import settings
from spuza.ui.copy_sheet import CopySheet
from spuza.ui.prefs_sheet import PrefsSheet
from test_ui_copy_sheet import make_card, wait_for  # noqa: F401  (also makes the QApplication)


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "logs"))


def run_sheet(tmp_path, ok=True, why="", pics=None, target="/Volumes/CARD", secs=2, interval=10, **kw):
    calls = []

    def fake_eject(mount):
        calls.append(mount)
        return ok, why

    sheet = CopySheet(pics or make_card(tmp_path), tmp_path / "out", eject_target=Path(target) if target else None, **kw)
    sheet.eject_fn = fake_eject
    sheet.countdown_seconds = secs
    sheet.countdown_interval_ms = interval
    quits, done = [], []
    sheet.quit_requested.connect(lambda: quits.append(1))
    sheet.finished.connect(done.append)
    sheet.copy_button.click()
    assert wait_for(lambda: done)
    return sheet, calls, quits


def test_organised_copy_and_result_line(tmp_path):
    sheet, calls, quits = run_sheet(tmp_path)
    out = tmp_path / "out"
    assert sorted(p.name for p in out.iterdir()) == ["ed", "raw", "soc"]
    assert (out / "soc" / "X0001.JPG").is_file() and (out / "raw" / "X0001.RAF").is_file()
    assert sheet.result_label.text().endswith("· raw 4, soc 1")
    assert "Copied 4 pictures (5 files)" in sheet.result_label.text()
    assert str(tmp_path / "logs") in sheet.log_label.text() and not list(out.rglob("*.log"))
    assert calls == [] and quits == [] and sheet.status_label.isHidden()  # defaults: no eject, no quit
    assert sheet.wait_idle()


def test_flat_when_organise_off(tmp_path):
    sheet, *_ = run_sheet(tmp_path, organise=False)
    assert "raw" not in sheet.result_label.text()
    assert not (tmp_path / "out" / "raw").exists()


def test_eject_then_quit_countdown_emits_quit_requested(tmp_path):
    sheet, calls, quits = run_sheet(tmp_path, eject_after=True, quit_after=True)
    assert wait_for(lambda: quits)
    assert calls == [Path("/Volumes/CARD")]
    assert sheet.status_label.text().startswith("Card ejected. Quitting in")
    assert quits == [1] and sheet.stay_button.isHidden()
    assert sheet.wait_idle()


def test_stay_cancels_the_quit(tmp_path):
    sheet, calls, quits = run_sheet(tmp_path, eject_after=True, quit_after=True, secs=5, interval=60_000)
    assert wait_for(lambda: "Card ejected" in sheet.status_label.text())
    assert sheet._countdown.isActive()
    sheet.stay_button.click()
    assert sheet.stay_button.isHidden() and "Quitting" not in sheet.status_label.text()
    assert not sheet._countdown.isActive() and quits == []
    sheet.wait_idle()


def test_quit_only_and_eject_only(tmp_path):
    sheet, calls, quits = run_sheet(tmp_path, quit_after=True)
    assert wait_for(lambda: quits) and calls == []
    assert sheet.status_label.text().startswith("Quitting in")
    sheet.wait_idle()
    (tmp_path / "e").mkdir()
    sheet, calls, quits = run_sheet(tmp_path / "e", eject_after=True)
    assert wait_for(lambda: sheet.status_label.text() == "Card ejected.")
    assert quits == [] and sheet.stay_button.isHidden() and not sheet._countdown.isActive()
    sheet.wait_idle()


def test_failed_eject_still_quits(tmp_path):
    sheet, calls, quits = run_sheet(tmp_path, ok=False, why="busy", eject_after=True, quit_after=True)
    assert wait_for(lambda: quits)
    assert sheet.status_label.text().startswith("Could not eject the card: busy. Quitting in")
    sheet.wait_idle()


def test_eject_raising_is_a_failed_eject(tmp_path):
    def boom(mount):
        raise RuntimeError("kaput")

    sheet = CopySheet(make_card(tmp_path), tmp_path / "out", eject_after=True, eject_target=Path("/Volumes/CARD"))
    sheet.eject_fn = boom
    done = []
    sheet.finished.connect(done.append)
    sheet.copy_button.click()
    assert wait_for(lambda: done)
    assert wait_for(lambda: "Could not eject the card: RuntimeError: kaput" in sheet.status_label.text())
    sheet.wait_idle()


def test_no_target_with_eject_on(tmp_path):
    sheet, calls, quits = run_sheet(tmp_path, target=None, eject_after=True, quit_after=True)
    assert wait_for(lambda: quits)
    assert calls == [] and sheet.status_label.text().startswith("Not opened from a card: nothing ejected")
    sheet.wait_idle()


def test_failed_file_means_nothing_ejected_nothing_quit(tmp_path):
    pics = make_card(tmp_path)
    missing = pics[0].files[0]
    sheet = CopySheet(pics, tmp_path / "out", eject_after=True, quit_after=True, eject_target=Path("/Volumes/CARD"))
    calls, quits, done = [], [], []
    sheet.eject_fn = lambda m: calls.append(m) or (True, "")
    sheet.quit_requested.connect(lambda: quits.append(1))
    sheet.finished.connect(done.append)
    sheet.countdown_interval_ms = 10
    missing.unlink()  # vanishes between plan and copy
    sheet.copy_button.click()
    assert wait_for(lambda: done)
    sheet.wait_idle()
    assert done[0].failed
    assert wait_for(lambda: False, 0.3) is False
    assert calls == [] and quits == [] and sheet.status_label.isHidden() and sheet.stay_button.isHidden()


def test_identical_skips_are_a_success(tmp_path):
    pics = make_card(tmp_path)
    first = CopySheet(pics, tmp_path / "out")
    d = []
    first.finished.connect(d.append)
    first.copy_button.click()
    assert wait_for(lambda: d)
    first.wait_idle()
    sheet, calls, quits = run_sheet(tmp_path, pics=pics, eject_after=True)
    assert sheet.result is not None and len(sheet.result.skipped) == 5
    assert wait_for(lambda: calls)
    sheet.wait_idle()


def test_cancelled_copy_ejects_nothing(tmp_path, monkeypatch):
    from spuza import copy as copy_mod

    def cancelled(plan, progress=None, cancel=None, log_path=None):
        return copy_mod.CopyResult(cancelled=True)

    monkeypatch.setattr(copy_mod, "run_copy", cancelled)
    sheet, calls, quits = run_sheet(tmp_path, eject_after=True, quit_after=True)
    assert wait_for(lambda: False, 0.2) is False
    assert calls == [] and quits == [] and sheet.status_label.isHidden()
    sheet.wait_idle()


def test_closing_the_sheet_cancels_a_pending_quit(tmp_path):
    sheet, calls, quits = run_sheet(tmp_path, quit_after=True, secs=5, interval=60_000)
    assert sheet._countdown.isActive()
    sheet.close()
    assert not sheet._countdown.isActive() and quits == []
    sheet.wait_idle()


# ---- Preferences rows --------------------------------------------------------------------------
def test_prefs_three_classic_rows_default_on_and_visible_without_advanced():
    sheet = PrefsSheet(12, 8)
    sheet.show()
    try:
        assert sheet.organise() and sheet.eject_after() and sheet.quit_after()
        assert (sheet.seeded_organise, sheet.seeded_eject, sheet.seeded_quit) == (True, True, True)
        for box, text in ((sheet.organise_check, "Copy into raw / soc / ed folders"),
                          (sheet.eject_check, "Eject the card after copying"),
                          (sheet.quit_check, "Quit Spuza after copying")):
            assert box.text() == text and box.isVisible()
        assert not sheet.threads_spin.isVisible()  # the advanced rows are still hidden
    finally:
        sheet.close()


def test_prefs_seeded_values_and_reset_sets_them_on():
    sheet = PrefsSheet(12, 8, organise=False, eject_after=False, quit_after=False)
    assert not sheet.organise() and not sheet.eject_after() and not sheet.quit_after()
    assert (sheet.seeded_organise, sheet.seeded_eject, sheet.seeded_quit) == (False, False, False)
    sheet.reset_button.click()
    assert sheet.organise() and sheet.eject_after() and sheet.quit_after()
    assert not settings.open_settings().contains(settings.COPY_ORGANISE)  # nothing written by the sheet

