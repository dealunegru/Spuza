"""Headless end-to-end: scan + Catalog + Prefetcher with the REAL decoder, then the scripted bench."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from spuza.catalog import Catalog, Prefetcher, scan
from spuza.cli import main

APP = QApplication.instance() or QApplication([])  # see test_ui_window: must precede test_catalog's fixture


def make_samples(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate([(200, 30, 30), (30, 200, 30), (30, 30, 200)]):
        Image.new("RGB", (320, 240), c).save(root / f"IMG_{i:04d}.jpg", quality=85)
    Image.new("RGB", (160, 120), (90, 90, 90)).save(root / "IMG_0003.png")
    # synthetic pair: a JPEG beside a raw-named stem. The "raw" is not a real raw, so it must fail
    # cleanly while its pair still counts as one picture.
    shutil.copy(root / "IMG_0000.jpg", root / "IMG_0004.jpg")
    (root / "IMG_0004.RAF").write_bytes(b"not a raf")
    for n, p in enumerate(sorted(root.iterdir(), key=lambda q: q.name)):
        os.utime(p, (1_700_000_000 + n, 1_700_000_000 + n))
    return root


def wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        APP.processEvents()
        time.sleep(0.005)
    return cond()


def run_prefetch(root: Path):
    cat = Catalog(scan(root))
    pf = Prefetcher(cat)
    try:
        pf.update(0)
        n = len(cat)
        assert wait(lambda: all(pf.cached(i) is not None or pf.failed_reason(i) for i in range(min(n, 9))))
        return cat, {i: pf.cached(i) for i in range(n)}, {i: pf.failed_reason(i) for i in range(n)}
    finally:
        pf.shutdown()


def test_real_decoder_on_pillow_samples(tmp_path):
    cat, images, failed = run_prefetch(make_samples(tmp_path / "s"))
    assert len(cat) == 5
    ok = [i for i, im in images.items() if im is not None]
    assert len(ok) == 4
    assert all(isinstance(images[i], QImage) and not images[i].isNull() for i in ok)
    bad = [i for i, r in failed.items() if r]
    assert len(bad) == 1 and cat.pictures[bad[0]].kind == "RAF+JPG"


def test_real_decoder_on_real_set(real_set):
    cat, images, failed = run_prefetch(real_set)
    assert len(cat) == 6
    assert all(im is not None and not im.isNull() for im in images.values()), failed


def test_bench_exits_zero_offscreen(tmp_path, capsys):
    root = make_samples(tmp_path / "s")
    assert main(["--demo", str(root), "--bench"]) == 0
    err = capsys.readouterr().err
    assert "first image" in err and "total" in err
    assert err.count("picture ") >= 5


def test_bench_on_empty_folder_exits_1(tmp_path):
    (tmp_path / "e").mkdir()
    assert main(["--demo", str(tmp_path / "e"), "--bench"]) == 1
