"""Tests for spuza.preview: real camera files (skipped when absent) plus Pillow-made samples."""

import io
import struct
import time

import pytest
from PIL import Image

from spuza import preview as pv
from spuza.preview import PreviewError, load

from conftest import DNG_NAMES, RAF_NAME


def _jpeg(size=(200, 100), orientation=None, color=(200, 30, 30)) -> bytes:
    im = Image.new("RGB", size, color)
    buf = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[274] = orientation
        im.save(buf, "JPEG", exif=exif)
    else:
        im.save(buf, "JPEG")
    return buf.getvalue()


# ---- real files -------------------------------------------------------------------------------

def test_real_raf(real_set):
    path = real_set / RAF_NAME
    im, info = load(path)
    assert info.source == "raf-embedded"
    assert im.mode == "RGB"
    assert (info.width, info.height) == (1920, 1280)
    assert info.nbytes == 602203
    assert info.ms >= 0
    rawpy = pytest.importorskip("rawpy")
    with rawpy.imread(str(path)) as r:
        thumb = bytes(r.extract_thumb().data)
    data, kind = pv.extract_embedded(path)
    assert data == thumb and kind == "raf-embedded"
    assert pv.sensor_size(path) == (6032, 4028)
    assert pv.needs_full_decode(path, info) is True


@pytest.mark.parametrize("name", DNG_NAMES)
def test_real_dng(real_set, name):
    path = real_set / name
    im, info = load(path)
    assert info.source == "dng-embedded"
    assert max(info.width, info.height) >= 5000
    assert im.mode == "RGB"
    assert max(im.size) <= 2560 * 2  # draft may keep up to 2x
    assert max(im.size) >= 2560
    assert pv.needs_full_decode(path, info) is False
    assert pv.report(path)["source"] == "dng-embedded"


def test_real_full_decode_half(real_set):
    im = pv.full_decode(real_set / RAF_NAME, half_size=True)
    assert im.mode == "RGB" and max(im.size) > 2500


# ---- open formats -----------------------------------------------------------------------------

def test_jpeg_orientation_applied(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(_jpeg((200, 100), orientation=6))
    im, info = load(p)
    assert im.size == (100, 200)
    assert info.source == "pillow"
    assert (info.width, info.height) == (200, 100)
    assert info.nbytes == p.stat().st_size


def test_jpeg_draft_and_full(tmp_path):
    p = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 2000), (1, 2, 3)).save(p, "JPEG")
    im, info = load(p, max_side=1000)
    assert max(im.size) >= 1000 and max(im.size) < 4000
    assert (info.width, info.height) == (4000, 2000)
    im2, _ = load(p, max_side=None)
    assert im2.size == (4000, 2000)


@pytest.mark.parametrize("ext,fmt", [("png", "PNG"), ("gif", "GIF"), ("bmp", "BMP"),
                                     ("tif", "TIFF")])
def test_open_formats(tmp_path, ext, fmt):
    p = tmp_path / f"x.{ext}"
    Image.new("RGB", (64, 32), (10, 200, 10)).save(p, fmt)
    im, info = load(p)
    assert im.mode == "RGB" and im.size == (64, 32)
    assert info.source == "pillow"


def test_gif_first_frame(tmp_path):
    p = tmp_path / "anim.gif"
    a = Image.new("RGB", (20, 20), (255, 0, 0))
    b = Image.new("RGB", (20, 20), (0, 0, 255))
    a.save(p, "GIF", save_all=True, append_images=[b])
    im, _ = load(p)
    r, g, bl = im.getpixel((5, 5))
    assert r > 200 and bl < 50


def test_heic(tmp_path):
    ph = pytest.importorskip("pillow_heif")
    ph.register_heif_opener()
    p = tmp_path / "x.heic"
    Image.new("RGB", (64, 48), (50, 60, 70)).save(p, "HEIF")
    im, info = load(p)
    assert info.source == "heif" and im.mode == "RGB" and im.size == (64, 48)


def test_non_image_and_missing(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(b"this is not an image at all")
    with pytest.raises(PreviewError):
        load(p)
    with pytest.raises(PreviewError):
        load(tmp_path / "nope.jpg")
    q = tmp_path / "x.xyz"
    q.write_bytes(b"abc")
    with pytest.raises(PreviewError):
        load(q)


# ---- hostile raw containers -------------------------------------------------------------------

def test_truncated_raf(tmp_path):
    p = tmp_path / "t.RAF"
    head = bytearray(0x100)
    head[:15] = b"FUJIFILMCCD-RAW"
    head[0x54:0x5C] = struct.pack(">II", 0x100, 5_000_000)  # length far beyond the file
    p.write_bytes(bytes(head))
    assert pv.extract_embedded(p) is None
    with pytest.raises(PreviewError):
        load(p)


def test_raf_wrong_magic_and_tiny(tmp_path):
    p = tmp_path / "w.raf"
    p.write_bytes(b"xx")
    assert pv.extract_embedded(p) is None
    with pytest.raises(PreviewError):
        load(p)


def _cyclic_tiff(path):
    # little-endian TIFF, IFD0 at 8 with 0 entries whose next-IFD pointer points back to 8
    path.write_bytes(b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 0) + struct.pack("<I", 8))


def test_cyclic_ifd_chain(tmp_path):
    p = tmp_path / "c.dng"
    _cyclic_tiff(p)
    t0 = time.perf_counter()
    assert pv.extract_embedded(p) is None
    with pytest.raises(PreviewError):
        load(p)
    assert time.perf_counter() - t0 < 10


def test_ifd_offset_out_of_bounds(tmp_path):
    p = tmp_path / "o.dng"
    p.write_bytes(b"II*\x00" + struct.pack("<I", 0xFFFFFF00) + b"\0" * 16)
    assert pv.extract_embedded(p) is None


def test_dng_synthetic_jpegif(tmp_path):
    """A minimal DNG-like TIFF with a JPEGInterchangeFormat pair in IFD0 is sliced and oriented."""
    jpg = _jpeg((120, 60))
    n = 3
    data_off = 8 + 2 + n * 12 + 4
    ifd = struct.pack("<H", n)
    ifd += struct.pack("<HHII", 274, 3, 1, 6)  # orientation 6 in value slot
    ifd += struct.pack("<HHII", 513, 4, 1, data_off)
    ifd += struct.pack("<HHII", 514, 4, 1, len(jpg))
    ifd += struct.pack("<I", 0)
    p = tmp_path / "s.dng"
    p.write_bytes(b"II*\x00" + struct.pack("<I", 8) + ifd + jpg)
    got = pv.extract_embedded(p)
    assert got is not None and got[0] == jpg and got[1] == "dng-embedded"
    im, info = load(p)
    assert info.source == "dng-embedded" and im.size == (60, 120)


def test_report_error_is_a_value(tmp_path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"nope")
    r = pv.report(p)
    assert r["error"] and r["file"] == "bad.jpg"
    assert pv.sensor_size(p) is None
