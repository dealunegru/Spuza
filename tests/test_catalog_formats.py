"""Catalog scan never silently drops a file: companions, other files, unreadable entries, HIF, new raws.

Synthetic trees only (names and sizes). H05-H09 are the card simulations in
state/research/spuza/formats/cards-and-pairing.md (probes/cards/sim.py).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from spuza import catalog
from spuza.catalog import scan, scan_card


def mk(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def names(files) -> list[str]:
    return sorted(Path(f).name for f in files)


def test_h05_dual_slot_separate_pairs_within_one_directory_only(tmp_path):
    # RAW on slot 1, JPEG on slot 2: different directories never pair, and neither is a companion or "other".
    mk(tmp_path / "SLOT1" / "100_FUJI" / "DSCF0010.RAF")
    mk(tmp_path / "SLOT2" / "100_FUJI" / "DSCF0010.JPG")
    res = scan_card(tmp_path)
    assert sorted(p.kind for p in res.pictures) == ["JPG", "RAF only"]
    assert all(p.companions == () for p in res.pictures)
    assert res.others == () and res.unreadable == ()


def test_h06_dual_slot_backup_identical_cards_stay_separate_pictures(tmp_path):
    for slot in ("SLOT1", "SLOT2"):
        mk(tmp_path / slot / "DSCF0011.RAF", 100)
        mk(tmp_path / slot / "DSCF0011.JPG", 20)
    pics = scan(tmp_path)
    assert [p.kind for p in pics] == ["RAF+JPG", "RAF+JPG"]
    assert all(p.size_bytes == 120 for p in pics)


def test_h07_fuji_raf_plus_hif_is_one_picture(tmp_path):
    mk(tmp_path / "DSCF0020.RAF", 100)
    mk(tmp_path / "DSCF0020.HIF", 30)
    (pic,) = scan(tmp_path)
    assert pic.kind == "RAF+HIF"
    assert pic.primary.name == "DSCF0020.RAF"
    assert pic.sidecar is not None and pic.sidecar.name == "DSCF0020.HIF"
    assert names(pic.files) == ["DSCF0020.HIF", "DSCF0020.RAF"]
    assert pic.size_bytes == 130 and pic.companions == ()


def test_h07_raf_jpg_hif_is_one_picture_sidecar_stays_the_jpeg(tmp_path):
    mk(tmp_path / "DSCF0021.RAF", 100)
    mk(tmp_path / "DSCF0021.JPG", 20)
    mk(tmp_path / "DSCF0021.HIF", 30)
    (pic,) = scan(tmp_path)
    assert pic.kind == "RAF+JPG+HIF"
    assert pic.sidecar is not None and pic.sidecar.name == "DSCF0021.JPG"
    assert names(pic.files) == ["DSCF0021.HIF", "DSCF0021.JPG", "DSCF0021.RAF"]
    assert pic.size_bytes == 150


def test_hif_alone_is_its_own_picture(tmp_path):
    mk(tmp_path / "DSCF0022.HIF", 30)
    (pic,) = scan(tmp_path)
    assert pic.kind == "HIF" and pic.sidecar is None


def test_h08_canon_cr3_jpg_and_cr3_hif(tmp_path):
    mk(tmp_path / "IMG_0001.CR3", 100)
    mk(tmp_path / "IMG_0001.JPG", 20)
    mk(tmp_path / "IMG_0002.CR3", 100)
    mk(tmp_path / "IMG_0002.HIF", 30)
    by_stem = {p.stem: p for p in scan(tmp_path)}
    assert len(by_stem) == 2
    assert by_stem["IMG_0001"].kind == "CR3+JPG"
    assert by_stem["IMG_0002"].kind == "CR3+HIF"
    assert by_stem["IMG_0002"].primary.name == "IMG_0002.CR3"


@pytest.mark.parametrize("ext", [".arw", ".nef", ".nrw", ".pef", ".3fr", ".cr2", ".orf", ".rw2", ".cr3"])
def test_h09_other_vendor_raws_pair_with_jpeg(tmp_path, ext):
    mk(tmp_path / f"P0001{ext.upper()}", 100)
    mk(tmp_path / "P0001.JPG", 20)
    (pic,) = scan(tmp_path)
    assert pic.kind == f"{ext[1:].upper()}+JPG"
    assert pic.primary.suffix.lower() == ext
    assert pic.size_bytes == 120


def test_live_photo_mov_is_a_companion_not_a_picture(tmp_path):
    mk(tmp_path / "IMG_0100.HEIC", 100)
    mk(tmp_path / "IMG_0100.MOV", 500)
    res = scan_card(tmp_path)
    (pic,) = res.pictures
    assert pic.kind == "HEIC"
    assert names(pic.companions) == ["IMG_0100.MOV"]
    assert names(pic.files) == ["IMG_0100.HEIC", "IMG_0100.MOV"]
    assert pic.size_bytes == 600
    assert res.others == ()


def test_companions_by_stem_are_case_insensitive_and_any_extension(tmp_path):
    mk(tmp_path / "DSCF0030.RAF", 100)
    mk(tmp_path / "dscf0030.xmp", 5)
    mk(tmp_path / "DSCF0030.THM", 3)
    mk(tmp_path / "DSCF0030.WAV", 7)
    mk(tmp_path / "DSCF0030.weird", 1)
    (pic,) = scan(tmp_path)
    assert names(pic.companions) == ["DSCF0030.THM", "DSCF0030.WAV", "DSCF0030.weird", "dscf0030.xmp"]
    assert len(pic.files) == 5 and pic.size_bytes == 116


def test_other_files_not_on_a_picture_stem_are_reported(tmp_path):
    mk(tmp_path / "A.JPG")
    mk(tmp_path / "CLIP0001.MOV")
    mk(tmp_path / "notes.txt")
    mk(tmp_path / "sub" / "DSCF9.XMP")
    res = scan_card(tmp_path)
    assert [p.stem for p in res.pictures] == ["A"]
    assert names(res.others) == ["CLIP0001.MOV", "DSCF9.XMP", "notes.txt"]


def test_companion_in_another_directory_is_not_attached(tmp_path):
    mk(tmp_path / "a" / "X.JPG")
    mk(tmp_path / "b" / "X.XMP")
    res = scan_card(tmp_path)
    (pic,) = res.pictures
    assert pic.companions == ()
    assert names(res.others) == ["X.XMP"]


def test_dotfiles_and_system_dirs_stay_skipped(tmp_path):
    mk(tmp_path / "A.JPG")
    mk(tmp_path / "._A.JPG")
    mk(tmp_path / ".Spotlight-V100" / "x.dat")
    mk(tmp_path / "System Volume Information" / "y.dat")
    res = scan_card(tmp_path)
    assert len(res.pictures) == 1 and res.others == ()


def test_unreadable_entry_is_reported_not_dropped(tmp_path, monkeypatch):
    mk(tmp_path / "A.JPG")
    bad = mk(tmp_path / "B.JPG")
    real_scandir = os.scandir

    class Entry:
        def __init__(self, e):
            self._e = e
            self.name, self.path = e.name, e.path

        def is_dir(self, **kw):
            return self._e.is_dir(**kw)

        def is_symlink(self):
            return self._e.is_symlink()

        def stat(self, **kw):
            if self.name == "B.JPG":
                raise PermissionError("denied")
            return self._e.stat(**kw)

    class Scan:
        def __init__(self, d):
            self._it = real_scandir(d)

        def __enter__(self):
            return (Entry(e) for e in self._it.__enter__())

        def __exit__(self, *a):
            return self._it.__exit__(*a)

    monkeypatch.setattr(catalog.os, "scandir", Scan)
    res = scan_card(tmp_path)
    assert [p.stem for p in res.pictures] == ["A"]
    assert [Path(u).name for u in res.unreadable] == [bad.name]


def test_scan_returns_pictures_of_scan_card_deterministically(tmp_path):
    mk(tmp_path / "B.JPG")
    mk(tmp_path / "A.JPG")
    mk(tmp_path / "A.XMP")
    assert scan(tmp_path) == scan_card(tmp_path).pictures
    assert [p.stem for p in scan(tmp_path)] == [p.stem for p in scan(tmp_path)]
    assert isinstance(scan(tmp_path), list)


def test_extension_sets():
    for e in (".arw", ".nef", ".nrw", ".pef", ".3fr", ".cr2", ".orf", ".rw2", ".cr3", ".raf", ".dng"):
        assert e in catalog.RAW_EXTS
    assert ".hif" in catalog.IMAGE_EXTS
