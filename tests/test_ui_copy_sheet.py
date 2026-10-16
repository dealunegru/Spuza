"""CopySheet: list, totals, destination, space warning, real copy on a worker, cancel (offscreen)."""

import hashlib
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza.catalog import Picture, scan
from spuza.ui.copy_sheet import CopySheet


# Widgets need a QApplication, and test_catalog.py's session fixture makes a bare QGuiApplication that
# cannot be upgraded later (the process then dies in the first widget). Creating the QApplication at
# collection time, before any fixture runs, is the only way to be order-proof without touching
# conftest.py; a QApplication still serves the QGuiApplication tests. Move into conftest when owned.
_APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _logs_dir(tmp_path, monkeypatch):
    """Copy logs go to a per-test folder, never the real app-data one (and never the destination)."""
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "spuza-logs"))


@pytest.fixture(scope="module")
def qapp():
    return _APP


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        QTest.qWait(5)
    return cond()


def make_card(tmp_path: Path, n=3, size=300_000) -> list[Picture]:
    src = tmp_path / "card"
    src.mkdir()
    for i in range(n):
        (src / f"R000{i}.DNG").write_bytes(bytes([i + 1]) * size)
    (src / "X0001.RAF").write_bytes(b"r" * size)
    (src / "X0001.JPG").write_bytes(b"j" * (size // 10))
    return scan(src)


def digests(root: Path) -> dict[str, str]:
    return {str(p): hashlib.sha1(p.read_bytes()).hexdigest() for p in sorted(root.iterdir())}


def test_lists_total_and_destination(qapp, tmp_path):
    pics = make_card(tmp_path)
    sheet = CopySheet(pics, tmp_path / "out")
    texts = [sheet.list.item(r).text() for r in range(sheet.list.count())]
    assert len(texts) == 4
    assert any(t.startswith("X0001 · RAF+JPG · 330 KB") for t in texts)
    assert any(t.startswith("R0000 · DNG only · 300 KB") for t in texts)
    assert sheet.total_label.text() == "4 pictures · 5 files · 1.2 MB"
    assert sheet.dest_label.text() == str(tmp_path / "out")
    assert "free at the destination" in sheet.free_label.text()
    assert sheet.plan_label.text() == "Will copy 5 files, skip 0 already there"
    assert sheet.copy_button.isEnabled()
    assert sheet.windowModality().name == "WindowModal"


def test_no_destination_disables_copy(qapp, tmp_path):
    sheet = CopySheet(make_card(tmp_path))
    assert sheet.dest_label.text() == "No folder chosen"
    assert not sheet.copy_button.isEnabled()


def test_folder_chooser_is_injectable(qapp, tmp_path):
    sheet = CopySheet(make_card(tmp_path))
    asked = []
    sheet.choose_folder = lambda parent, title, start: asked.append(start) or str(tmp_path / "picked")
    sheet.choose_button.click()
    assert sheet.dest == tmp_path / "picked"
    assert sheet.copy_button.isEnabled()
    sheet.choose_folder = lambda *a: ""  # cancelled dialog keeps the old destination
    sheet.choose_button.click()
    assert sheet.dest == tmp_path / "picked" and len(asked) == 1


def test_space_short_warns_and_disables(qapp, tmp_path):
    sheet = CopySheet(make_card(tmp_path), tmp_path / "out")
    sheet.free_space_fn = lambda dest: 100
    sheet._update_dest()
    assert "Not enough space" in sheet.free_label.text()
    assert not sheet.copy_button.isEnabled()
    sheet.start_copy()  # even called directly it must not run
    assert not sheet.running and not (tmp_path / "out").exists()
    sheet.free_space_fn = lambda dest: 10**12
    sheet._update_dest()
    assert sheet.copy_button.isEnabled()


def test_free_space_error_is_unknown_not_crash(qapp, tmp_path):
    def boom(dest):
        raise OSError("gone")

    sheet = CopySheet(make_card(tmp_path), tmp_path / "out")
    sheet.free_space_fn = boom
    sheet._update_dest()
    assert sheet.free_label.text() == "Free space unknown"


def test_real_copy_through_worker(qapp, tmp_path):
    pics = make_card(tmp_path)
    before = digests(tmp_path / "card")
    out = tmp_path / "out"
    sheet = CopySheet(pics, out, organise=False)
    ticks, results, started = [], [], []
    sheet.progress.connect(lambda d, t: ticks.append((d, t)))
    sheet.finished.connect(results.append)
    sheet.started.connect(lambda: started.append(1))
    sheet.copy_button.click()
    assert started == [1]
    assert wait_for(lambda: results)
    res = results[0]
    assert len(res.copied) == 5 and not res.failed and not res.cancelled
    assert ticks and ticks[-1][0] == ticks[-1][1] > 0
    assert sheet.bar.value() == sheet.bar.maximum()
    assert sheet.result_label.text() == "Copied 4 pictures (5 files) · skipped 0 · failed 0"
    assert str(res.log_path) in sheet.log_label.text() and res.log_path.is_file()
    assert sorted(p.name for p in out.iterdir() if p.suffix != ".log") == sorted(
        p.name for p in (tmp_path / "card").iterdir()
    )
    assert digests(tmp_path / "card") == before  # sources untouched
    assert sheet.wait_idle()
    # opening the folder is injectable
    urls = []
    sheet.open_url = urls.append
    sheet.show_button.click()
    assert urls == [QUrl.fromLocalFile(str(out))]


def test_second_run_skips_what_is_there(qapp, tmp_path):
    pics = make_card(tmp_path, n=2)
    out = tmp_path / "out"
    for _ in range(2):
        sheet = CopySheet(pics, out)
        results = []
        sheet.finished.connect(results.append)
        sheet.copy_button.click()
        assert wait_for(lambda: results)
        sheet.wait_idle()
    assert sheet.plan is not None
    assert len(results[0].skipped) == 4 and not results[0].copied
    assert "skipped 4" in sheet.result_label.text()


def test_progress_is_throttled(qapp, tmp_path):
    pics = make_card(tmp_path, n=1, size=4_000_000)  # several 1 MiB chunks
    sheet = CopySheet(pics, tmp_path / "out")
    sheet.clock = lambda: 0.0  # frozen clock: only the final update may pass the throttle
    ticks, results = [], []
    sheet.progress.connect(lambda d, t: ticks.append(d))
    sheet.finished.connect(results.append)
    sheet.copy_button.click()
    assert wait_for(lambda: results)
    sheet.wait_idle()
    assert 1 <= len(ticks) <= 3
    assert ticks[-1] > 0


def test_cancel_stops_and_leaves_no_partial(qapp, tmp_path):
    pics = make_card(tmp_path, n=2, size=3_000_000)
    out = tmp_path / "out"
    sheet = CopySheet(pics, out)
    results = []
    sheet.finished.connect(results.append)
    sheet.progress.connect(lambda d, t: sheet.cancel())  # cancel at the first progress report
    sheet.copy_button.click()
    assert wait_for(lambda: results)
    sheet.wait_idle()
    assert results[0].cancelled
    assert "cancelled" in sheet.result_label.text()
    assert not list(out.glob("*.spuza-part")) and not list(out.glob(".*spuza-part"))
    assert digests(tmp_path / "card")  # sources still there


def test_cancel_when_idle_rejects(qapp, tmp_path):
    sheet = CopySheet(make_card(tmp_path), tmp_path / "out")
    sheet.show()
    sheet.cancel_button.click()
    assert not sheet.isVisible()


def test_close_mid_copy_asks_first(qapp, tmp_path):
    pics = make_card(tmp_path, n=2, size=3_000_000)
    sheet = CopySheet(pics, tmp_path / "out")
    sheet.show()
    answers = []
    sheet.confirm_close = lambda: answers.append(1) or False  # user says: keep copying
    results = []
    sheet.finished.connect(results.append)
    sheet.copy_button.click()
    sheet.close()
    assert answers == [1] and sheet.isVisible()
    assert wait_for(lambda: results)
    sheet.wait_idle()
    assert not results[0].cancelled
    # now say yes while a second copy runs
    sheet2 = CopySheet(pics, tmp_path / "out2")
    sheet2.show()
    sheet2.confirm_close = lambda: True
    r2 = []
    sheet2.finished.connect(r2.append)
    sheet2.copy_button.click()
    sheet2.close()
    assert not sheet2.isVisible()
    assert sheet2.wait_idle()
