"""Bugfinder round 1: B14 (default copy destination) and B15 (.app version metadata)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from spuza.catalog import Catalog, Prefetcher
from spuza.ui.window import MainWindow

from PIL import Image
from PySide6.QtWidgets import QApplication

from spuza.catalog import Picture
from spuza.preview import PreviewInfo

APP = QApplication.instance() or QApplication([])
ROOT = Path("/cards/X-PRO2")
PICS = [Picture("A0", ROOT / "A0.JPG", None, (ROOT / "A0.JPG",), "JPG", 1000, 1.0)]


def fake_loader(path, max_side):
    return Image.new("RGB", (64, 48)), PreviewInfo(path, "raf-embedded", 1920, 1280, 1000, 3.5)


def pump(cond, timeout=5.0):
    import time
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond()

SPEC = Path(__file__).resolve().parent.parent / "packaging" / "spuza.spec"


def test_default_copy_dest_is_home_pictures_not_the_card(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cat = Catalog(PICS)
    w = MainWindow(cat, Prefetcher(cat, loader=fake_loader), ROOT, clock=lambda: 100.0)
    seen = {}

    class Sheet:
        def __init__(self, pictures, default_dest=None, parent=None):
            seen["dest"] = default_dest

        def open(self):
            pass

    w.copy_sheet_factory = Sheet
    try:
        w.show()
        pump(lambda: w.viewer.has_image)
        QTest.keyClick(w, Qt.Key_M)
        QTest.keyClick(w, Qt.Key_C)
    finally:
        w.close()
    dest = Path(seen["dest"])
    assert dest == tmp_path / "Pictures" / "Spuza"
    assert ROOT not in dest.parents and dest != ROOT


def test_spec_bundles_spuza_dist_info():
    text = SPEC.read_text(encoding="utf-8")
    assert "copy_metadata" in text
    assert 'copy_metadata("spuza")' in text
    # the metadata must feed the datas list handed to Analysis
    before = text.split('copy_metadata("spuza")')[0].splitlines()[-1]
    assert "datas" in before
