"""Bugfinder round 1: 16-bit grayscale decode, misnamed raws, scandir errors mid-iteration."""

from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from spuza import catalog, preview


def _gradient16() -> Image.Image:
    row = np.arange(0, 65536, 256, dtype=np.uint16)
    return Image.fromarray(np.tile(row, (256, 1)))


@pytest.mark.parametrize("ext", [".png", ".tif"])
def test_16bit_grayscale_scales_to_ramp(tmp_path, ext):
    p = tmp_path / f"g16{ext}"
    _gradient16().save(p)
    im, info = preview.load(p, max_side=None)
    assert info.source == "pillow"
    row = np.asarray(im)[0, :, 0].astype(int)
    assert row[0] == 0
    assert row[-1] >= 250
    assert len(set(row.tolist())) > 200  # a ramp, not clipped to 0/255
    assert np.all(np.diff(row) >= 0)


def test_misnamed_jpeg_with_raw_extension_falls_back_to_pillow(tmp_path):
    p = tmp_path / "IMG_0001.DNG"
    Image.new("RGB", (32, 24), (200, 10, 10)).save(p, format="JPEG")
    im, info = preview.load(p)
    assert info.source == "pillow"
    assert im.size == (32, 24)


class _FlakyScandir:
    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        yield from self.entries
        raise OSError("card reader hiccup")


def test_scan_keeps_pictures_collected_before_scandir_error(tmp_path, monkeypatch):
    for n in ("a.jpg", "b.jpg"):
        (tmp_path / n).write_bytes(b"x")
    real = os.scandir
    entries = list(real(tmp_path))
    monkeypatch.setattr(catalog.os, "scandir", lambda d: _FlakyScandir(entries))
    pics = catalog.scan(tmp_path)
    assert sorted(p.primary.name for p in pics) == ["a.jpg", "b.jpg"]
