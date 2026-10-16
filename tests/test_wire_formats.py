"""The merged formats round wired together: CLI scan_card, window -> copy sheet, inspector wording."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza import cli
from spuza.catalog import Catalog, Picture, Prefetcher, scan_card
from spuza.preview import PreviewInfo
from spuza.ui.copy_sheet import CopySheet
from spuza.ui.inspector import Inspector, explain
from spuza.ui.window import MainWindow

APP = QApplication.instance() or QApplication([])


def make_card(root: Path, files: dict[str, int]) -> Path:
    d = root / "DCIM" / "100TEST"
    d.mkdir(parents=True)
    for name, size in files.items():
        (d / name).write_bytes(b"x" * size)
    return root


def fake_loader(path, max_side):
    return Image.new("RGB", (64, 48)), PreviewInfo(path, "pillow", 64, 48, 1000, 1.0)


def pump(cond, timeout=5.0):
    import time

    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond()


def _pic(kind, names, companions=()):
    files = tuple(Path("/c") / n for n in names) + tuple(Path("/c") / n for n in companions)
    return Picture("X", files[0], files[1] if len(names) > 1 else None, files, kind, 10, 0.0,
                   companions=tuple(Path("/c") / n for n in companions))


# --- cli ----------------------------------------------------------------------------------------

def test_open_card_run_keeps_others_and_unreadable(tmp_path):
    a = make_card(tmp_path / "A", {"IMG_1.JPG": 10, "stray.txt": 3})
    run = cli.resolve_cards([a], None, say=lambda t: None)
    cat, _note = cli.open_card_run(run)
    assert [p.name for p in cat.others] == ["stray.txt"] and cat.unreadable == []


def test_open_card_run_multi_card_keeps_others(tmp_path):
    a = make_card(tmp_path / "A", {"IMG_1.JPG": 10, "stray.txt": 3})
    b = make_card(tmp_path / "B", {"IMG_1.JPG": 10, "other.txt": 3})
    cat, _ = cli.open_card_run(cli.resolve_cards([a, b], None, say=lambda t: None))
    assert [p.name for p in cat.others] == ["stray.txt"] and cat.pictures[0].twins


def test_open_card_run_passes_unreadable(tmp_path):
    a = make_card(tmp_path / "A", {"IMG_1.JPG": 10})
    bad = a / "DCIM" / "bad"

    def fake(root):
        res = scan_card(root)
        return type(res)(res.pictures, res.others, (bad,))

    cat, _ = cli.open_card_run(cli.resolve_cards([a], None, say=lambda t: None), scan=fake)
    assert cat.unreadable == [bad]


def test_cli_has_no_choose_root():
    assert not hasattr(cli, "choose_root")


# --- window -> copy sheet -----------------------------------------------------------------------

def test_window_copy_sheet_gets_others_unreadable_and_twins(tmp_path):
    a = make_card(tmp_path / "A", {"IMG_1.JPG": 10, "stray.txt": 3})
    b = make_card(tmp_path / "B", {"IMG_1.JPG": 10})
    cat, _ = cli.open_card_run(cli.resolve_cards([a, b], None, say=lambda t: None))
    cat.unreadable.append(a / "DCIM" / "bad")
    w = MainWindow(cat, Prefetcher(cat, loader=fake_loader), a / "DCIM", resume=False)
    w.show()
    pump(lambda: w.viewer.has_image)
    sheets = []
    orig = w._default_copy_sheet_factory

    def spy(*args, **kw):
        s = orig(*args, **kw)
        sheets.append(s)
        return s

    w.copy_sheet_factory = spy
    QTest.keyClick(w, Qt.Key_M)
    QTest.keyClick(w, Qt.Key_C)
    try:
        s = sheets[0]
        assert isinstance(s, CopySheet)
        assert [p.name for p in s.others] == ["stray.txt"] and len(s.unreadable) == 1
        assert s.total_twins == 1 and "twin" in s._twin_note()
        s.close()
    finally:
        w.close()


# --- inspector ----------------------------------------------------------------------------------

def test_explain_raf_hif():
    head, amber = explain(_pic("RAF+HIF", ["X.RAF", "X.HIF"]))
    assert head == "RAF + HIF: one picture, both files copy together" and not amber


def test_explain_raf_jpg_hif():
    head, amber = explain(_pic("RAF+JPG+HIF", ["X.RAF", "X.JPG", "X.HIF"]))
    assert head.startswith("RAF + JPG + HIF: one picture") and not amber


def test_explain_raf_jpg_unchanged():
    assert explain(_pic("RAF+JPG", ["X.RAF", "X.JPG"])) == ("RAF + JPG: one picture, both files copy together", False)


def test_explain_lone_raw_with_companions_is_amber_and_names_them():
    head, amber = explain(_pic("RAF only", ["X.RAF"], ["X.XMP", "X.MOV"]))
    assert amber and head.startswith("RAF only: no JPG beside it")
    assert "2 companion files (XMP, MOV) copy with it" in head


def test_explain_companions_on_pair():
    head, amber = explain(_pic("RAF+JPG", ["X.RAF", "X.JPG"], ["X.XMP"]))
    assert not amber and "1 companion file (XMP) copies with it" in head


def test_explain_hif_alone():
    assert explain(_pic("HIF", ["X.HIF"])) == ("HIF: one file", False)


def test_inspector_marks_companion_files():
    insp = Inspector()
    insp.set_picture(_pic("RAF+JPG", ["X.RAF", "X.JPG"], ["X.XMP"]))
    assert "X.XMP  (companion)" in insp.plain() or any(
        ln.startswith("X.XMP") and "companion" in ln for ln in insp.lines)
    assert not any("companion" in ln for ln in insp.lines if ln.startswith("X.RAF"))


def test_catalog_unused_import_guard():
    assert Catalog([]).others == []
