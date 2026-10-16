"""Copy sheet shows and reports everything that travels (companions) or stays behind (twins, others)."""

from pathlib import Path

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza.catalog import Picture
from spuza.copy import plan_copy
from spuza.ui.copy_sheet import CopySheet

_APP = QApplication.instance() or QApplication([])


def wait_for(cond, timeout=10.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        QTest.qWait(5)
    return cond()


def _w(p: Path, n: int) -> Path:
    p.write_bytes(b"x" * n)
    return p


def make(tmp_path: Path, stem="IMG_0001", with_twin=False, companions=("xmp", "mov")) -> Picture:
    src = tmp_path / "card"
    src.mkdir(exist_ok=True)
    prim = _w(src / f"{stem}.RAF", 1000)
    comps = tuple(_w(src / f"{stem}.{e}", 100) for e in companions)
    twins = ()
    if with_twin:
        card2 = tmp_path / "card2"
        card2.mkdir(exist_ok=True)
        twins = (_w(card2 / prim.name, 1000),)
    files = (prim, *comps)
    return Picture(stem, prim, None, files, "RAF only", 1000 + 100 * len(comps), 0.0, comps, twins)


def finish(sheet):
    sheet.start_copy()
    assert wait_for(lambda: sheet.result is not None)
    sheet.wait_idle()


def test_plan_total_counts_companions(tmp_path):
    pics = [make(tmp_path, "A"), make(tmp_path, "B", companions=("xmp",))]
    sheet = CopySheet(pics, tmp_path / "out")
    assert sheet.total_label.text() == "2 pictures · 5 files (3 companions) · 2.3 KB"
    assert sheet.plan_label.text().startswith("Will copy 5 files")


def test_no_companion_clause_when_none(tmp_path):
    sheet = CopySheet([make(tmp_path, companions=())], tmp_path / "out")
    assert "companion" not in sheet.total_label.text()


def test_companions_copied_and_reported(tmp_path):
    out = tmp_path / "out"
    sheet = CopySheet([make(tmp_path)], out)
    finish(sheet)
    assert sorted(f.name for f in out.iterdir() if not f.name.endswith(".log")) == [
        "IMG_0001.RAF", "IMG_0001.mov", "IMG_0001.xmp"]
    assert sheet.result_label.text().startswith("Copied 1 picture (3 files, 2 companions)")


def test_companion_name_collision_renames_whole_group(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _w(out / "IMG_0001.xmp", 5)  # a different file already there
    plan = plan_copy([make(tmp_path).files], out)
    assert sorted(i.dst.name for i in plan.items) == ["IMG_0001-1.RAF", "IMG_0001-1.mov", "IMG_0001-1.xmp"]
    assert (out / "IMG_0001.xmp").read_bytes() == b"x" * 5


def test_twins_reported_never_copied(tmp_path):
    out = tmp_path / "out"
    p = make(tmp_path, with_twin=True)
    sheet = CopySheet([p], out)
    msg = "1 twin file on the second card not copied (same as the first)"
    assert msg in sheet.plan_label.text()
    finish(sheet)
    assert msg in sheet.result_label.text()
    assert not any(f.parent == tmp_path / "card2" for i in sheet.plan.items for f in [i.src])
    assert (tmp_path / "card2" / "IMG_0001.RAF").exists()
    assert sorted(f.name for f in out.iterdir() if not f.name.endswith(".log")) == [
        "IMG_0001.RAF", "IMG_0001.mov", "IMG_0001.xmp"]


def test_twins_plural(tmp_path):
    ps = [make(tmp_path, "A", with_twin=True), make(tmp_path, "B", with_twin=True)]
    sheet = CopySheet(ps, tmp_path / "out")
    assert "2 twin files on the second card not copied (same as the first)" in sheet.plan_label.text()


def test_others_and_unreadable_shown(tmp_path):
    others = [tmp_path / "card" / "NOTES.TXT", tmp_path / "card" / "a.pdf"]
    bad = [tmp_path / "card" / "BAD.RAF"]
    sheet = CopySheet([make(tmp_path)], tmp_path / "out", others=others, unreadable=bad)
    t = sheet.notes_label.text()
    assert "2 other files on the card are not pictures and are not copied" in t
    assert "1 file could not be read" in t
    assert "NOTES.TXT" in sheet.notes_label.toolTip() and "BAD.RAF" in sheet.notes_label.toolTip()
    assert not any(f.name == "NOTES.TXT" for i in sheet.plan.items for f in [i.src])


def test_no_notes_when_nothing_left_behind(tmp_path):
    sheet = CopySheet([make(tmp_path)], tmp_path / "out")
    assert sheet.notes_label.text() == ""


def test_others_repeated_in_result(tmp_path):
    sheet = CopySheet([make(tmp_path)], tmp_path / "out", others=[tmp_path / "x.txt"])
    finish(sheet)
    assert "1 other file not copied (not a picture)" in sheet.result_label.text()


@pytest.mark.parametrize("bad", [None, [], ()])
def test_empty_others_tolerated(tmp_path, bad):
    sheet = CopySheet([make(tmp_path)], tmp_path / "out", others=bad, unreadable=bad)
    assert sheet.notes_label.text() == ""
