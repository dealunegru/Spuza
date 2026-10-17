"""Friend round 4: the picture's size of a raw with no JPG arrives after the image (Prefetcher.sized); the
window must refresh the inspector with it, for the picture on screen only."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PIL import Image

from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui.window import MainWindow

ROOT = Path("/cards/X")


def loader(path, max_side):
    return Image.new("RGB", (64, 48)), PreviewInfo(path, "raf-embedded", 1920, 1280, 10, 1.0)


def _win():
    pics = [Picture(f"P{i}", ROOT / f"P{i}.RAF", None, (ROOT / f"P{i}.RAF",), "RAF", 1_000_000, 1.0)
            for i in range(2)]
    cat = Catalog(pics)
    pf = Prefetcher(cat, loader=loader)
    return MainWindow(cat, pf, ROOT), cat, pf


def test_late_size_refreshes_the_inspector_for_the_current_picture():
    win, cat, pf = _win()
    try:
        info = replace(loader(ROOT / "P0.RAF", 0)[1], picture_size=(5998, 3997), picture_source="raw-frame")
        pf._info[cat.index] = info
        pf.sized.emit(cat.index, info)
        assert "Size: 5998 x 3997" in win.inspector.plain()
        assert "Preview: raf-embedded 1920 x 1280" in win.inspector.plain()
    finally:
        win.close()


def test_late_size_for_another_picture_leaves_the_inspector_alone():
    win, cat, pf = _win()
    try:
        before = win.inspector.plain()
        other = (cat.index + 1) % len(cat)
        pf.sized.emit(other, replace(loader(ROOT / "P1.RAF", 0)[1], picture_size=(1, 1), picture_source="raw-frame"))
        assert win.inspector.plain() == before
    finally:
        win.close()
