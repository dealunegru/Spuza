"""EXIF reading: synthetic Pillow JPEGs, the real camera files (skipped when absent), fail-soft."""

import io
import struct
from dataclasses import replace

import pytest
from PIL import Image

from spuza import exif as ex
from spuza import preview as pv

from conftest import DNG_NAMES, RAF_NAME


def _jpeg(fnum=2.8, exposure=0.004, iso=200, focal=18.0, model="Test Cam  ", exif=True) -> bytes:
    im = Image.new("RGB", (64, 48), (10, 120, 200))
    buf = io.BytesIO()
    if not exif:
        im.save(buf, "JPEG")
        return buf.getvalue()
    e = Image.Exif()
    if model is not None:
        e[0x0110] = model
    sub = e.get_ifd(0x8769)
    if fnum is not None:
        sub[0x829D] = fnum
    if exposure is not None:
        sub[0x829A] = exposure
    if iso is not None:
        sub[0x8827] = iso
    if focal is not None:
        sub[0x920A] = focal
    im.save(buf, "JPEG", exif=e)
    return buf.getvalue()


def test_synthetic_values():
    got = ex.read(_jpeg())
    assert got == ex.Exif(aperture="f/2.8", shutter="1/250 s", iso="ISO 200", focal="18 mm", model="Test Cam")


def test_no_exif_is_none(tmp_path):
    assert ex.read(_jpeg(exif=False)) is None
    p = tmp_path / "a.jpg"
    p.write_bytes(_jpeg(exif=False))
    assert ex.read(None, p) is None
    assert ex.read(None, None) is None


def test_partial_fields():
    got = ex.read(_jpeg(fnum=None, exposure=None, focal=None, model=None))
    assert got is not None and got.aperture is None and got.iso == "ISO 200" and got.model is None


@pytest.mark.parametrize("t, want", [(0.5, "1/2 s"), (1.0, "1 s"), (2.0, "2 s"), (2.5, "2.5 s"),
                                     (0.0015625, "1/640 s"), (0, None), (-1, None), (float("nan"), None)])
def test_shutter_format(t, want):
    assert ex.format_shutter(t) == want


def test_other_formats():
    assert ex.format_aperture(8.0) == "f/8" and ex.format_aperture(float("inf")) is None
    assert ex.format_focal(18.3) == "18.3 mm" and ex.format_focal(None) is None
    assert ex.format_iso((400, 400)) == "ISO 400" and ex.format_iso("x") is None
    assert ex.format_model(b"X-Pro2\x00 ") == "X-Pro2" and ex.format_model(5) is None


def test_garbage_input_never_raises(tmp_path):
    assert ex.read(b"not an image at all") is None
    assert ex.read(b"") is None
    bad = _jpeg()
    i = bad.index(b"Exif\x00\x00")
    mangled = bad[:i + 10] + b"\xff" * 40 + bad[i + 50:]
    ex.read(mangled)  # may or may not parse; must not raise
    p = tmp_path / "x.dng"
    p.write_bytes(b"II*\x00" + struct.pack("<I", 8) + b"\xff" * 100)
    assert ex.read(None, p) is None


def test_wrong_typed_tag_values():
    assert ex._num([]) is None and ex._num({}) is None and ex._num(True) is None


# ---- real files; values observed with a one-off read on 2026-10-16 -----------------------------

def test_real_dng_values(real_set):
    for name in DNG_NAMES:
        _im, info = pv.load(real_set / name)
        assert info.exif.taken, name  # each file carries its own date; the rest is the same
        assert replace(info.exif, taken=None) == ex.Exif("f/2.8", "1/640 s", "ISO 200", "18.3 mm", "RICOH GR III"), name


def test_real_raf_values(real_set):
    _im, info = pv.load(real_set / RAF_NAME)
    assert info.exif.taken
    assert replace(info.exif, taken=None) == ex.Exif("f/8", "1/850 s", "ISO 400", "35 mm", "X-Pro2")
