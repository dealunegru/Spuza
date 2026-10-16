"""Focus point (flag focus_point): the Fuji makernote reader, FocusInfo, and load()/inspector hooks."""

import io
import random
import statistics
import struct
import time
from pathlib import Path

import pytest
from PIL import Image

from spuza import features, focus, preview
from spuza.focus import FocusInfo
from spuza.preview import PreviewInfo
from spuza.ui.inspector import Inspector

SECOND_SET = Path("E:/tmp/tmp.3NuFKCKTwd/raws")
JES = Path("E:/tmp/spuza-test/jes")
NUL = b"\x00"


def _jpeg_with_exif(tiff: bytes, size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 90, 90)).save(buf, "JPEG")
    jpg = buf.getvalue()
    app1 = b"Exif" + NUL + NUL + tiff
    return jpg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpg[2:]


def _tiff(model=b"X-T5", mode=0, pixel=(10, 20), area_bits=0, orient=1, with_note=True, af=None) -> bytes:
    ents = [(0x1021, 3, 1, struct.pack("<HH", mode, 0)),
            (0x1023, 3, 2, struct.pack("<HH", *pixel)),
            (0x102D, 4, 1, struct.pack("<I", 0x4001 | (area_bits << 8)))]
    if af is not None:
        ents.append((0x1022, 3, 1, struct.pack("<HH", af, 0)))
    ents.sort()
    note = b"FUJIFILM" + struct.pack("<I", 12) + struct.pack("<H", len(ents))
    for tag, typ, cnt, val in ents:
        note += struct.pack("<HHI", tag, typ, cnt) + val
    note += NUL * 4
    mdl = model + NUL
    pad = NUL if len(mdl) & 1 else b""
    model_off = 8 + 2 + 3 * 12 + 4
    exif_off = model_off + len(mdl) + len(pad)
    note_off = exif_off + 2 + 12 + 4
    ifd0 = struct.pack("<H", 3)
    ifd0 += struct.pack("<HHII", 0x0110, 2, len(mdl), model_off)
    ifd0 += struct.pack("<HHIHH", 0x0112, 3, 1, orient, 0)
    ifd0 += struct.pack("<HHII", 0x8769, 4, 1, exif_off) + NUL * 4
    exif = struct.pack("<H", 1 if with_note else 0)
    if with_note:
        exif += struct.pack("<HHII", 0x927C, 7, len(note), note_off)
    exif += NUL * 4
    out = b"II*" + NUL + struct.pack("<I", 8) + ifd0 + mdl + pad + exif
    return out + (note if with_note else b"")


def _raf(path: Path, jpeg: bytes) -> Path:
    head = bytearray(b"FUJIFILMCCD-RAW 0201" + NUL * (0x100 - 20))
    head[0x54:0x5C] = struct.pack(">II", 0x100, len(jpeg))
    path.write_bytes(bytes(head) + jpeg)
    return path


# ---- synthetic reader -------------------------------------------------------------------------

def test_synthetic_roundtrip():
    fi = focus.from_jpeg(_jpeg_with_exif(_tiff(pixel=(30, 12), area_bits=1)))
    assert fi == FocusInfo("X-T5", "Auto", "Zone", (30, 12), (64, 48), 1)
    assert fi.usable() and fi.frac() == (30 / 64, 12 / 48)


def test_area_falls_back_to_afmode_and_manual_has_no_area():
    t = _tiff()
    assert focus.from_jpeg(_jpeg_with_exif(t)).area == "Single Point"
    fi = focus.from_jpeg(_jpeg_with_exif(_tiff(mode=1, pixel=(5, 5))))
    assert fi.mode == "Manual" and fi.area is None and not fi.usable() and fi.describe() == "Focus: manual"


def test_model_prefix_stripped():
    assert focus.from_jpeg(_jpeg_with_exif(_tiff(model=b"FUJIFILM X-T4"))).model == "X-T4"


@pytest.mark.parametrize("orient,expect", [
    (1, (0.25, 0.5)), (2, (0.75, 0.5)), (3, (0.75, 0.5)), (4, (0.25, 0.5)),
    (5, (0.5, 0.25)), (6, (0.5, 0.25)), (7, (0.5, 0.75)), (8, (0.5, 0.75)),
])
def test_frac_through_every_orientation(orient, expect):
    # a point at fx=.25, fy=.5 of the stored frame; displayed positions derived by hand
    fi = FocusInfo("X-T5", "Auto", "Single Point", (25, 50), (100, 100), orient)
    assert fi.frac() == pytest.approx(expect)


def test_rotation_corners():
    assert FocusInfo("X-T5", "Auto", None, (0, 0), (100, 50), 6).frac() == pytest.approx((1.0, 0.0))
    assert FocusInfo("X-T5", "Auto", None, (0, 0), (100, 50), 8).frac() == pytest.approx((0.0, 1.0))


def test_usable_rules():
    ok = FocusInfo("X-T5", "Auto", None, (10, 10), (100, 100))
    assert ok.usable() and ok.frac() is not None
    assert not FocusInfo("X-T5", "Auto", None, (100, 10), (100, 100)).usable()  # outside the frame
    assert not FocusInfo("X-T5", "Auto", None, (-1, 10), (100, 100)).usable()
    off = FocusInfo("X-Pro2", "Auto", None, (10, 10), (100, 100))
    assert not off.usable() and off.frac() is None  # not allowlisted
    assert not FocusInfo("X-T5", "Auto", None, None, (100, 100)).usable()
    assert not FocusInfo("X-T5", "Auto", None, (1, 1), None).usable()


def test_describe_texts():
    assert FocusInfo("X-T5", "Auto", "Single Point", (2207, 1472), (4416, 2944)).describe() == \
        "Focus: point (2207, 1472) · Auto Single Point"
    assert FocusInfo("X-T5", "Manual", None, (1, 1), (9, 9)).describe() == "Focus: manual"
    assert FocusInfo("X-T5", "Auto", None, None, None).describe() == "Focus: no point"
    assert FocusInfo("X-Pro2", "Auto", None, (1, 1), (9, 9)).describe() == "Focus: point not verified for X-Pro2"


def test_no_makernote_or_not_fuji_is_none():
    assert focus.from_jpeg(_jpeg_with_exif(_tiff(with_note=False))) is None
    assert focus.from_jpeg(b"\xff\xd8\xff\xd9") is None
    assert focus.from_jpeg(_jpeg_with_exif(_tiff().replace(b"FUJIFILM", b"NOTFUJI!"))) is None


def test_garbage_and_truncation_never_raise(tmp_path):
    good = _jpeg_with_exif(_tiff())
    for n in range(0, len(good), 3):
        focus.from_jpeg(good[:n])  # every prefix: None or a FocusInfo, never an exception
    rnd = random.Random(7)
    for _ in range(300):
        b = bytearray(good)
        for _ in range(rnd.randint(1, 12)):
            b[rnd.randrange(len(b))] = rnd.randrange(256)
        focus.from_jpeg(bytes(b))
    deep = b"\xff\xd8" + b"\xff\xe1\xff\xff" * 5000
    for junk in (None, 5, "str", b"", b"\xff\xd8", deep, b"\xff\xd8" + b"\xff" * 10**5):
        assert focus.from_jpeg(junk) is None
    for name, data in (("a.RAF", b""), ("b.RAF", b"FUJIFILMCCD-RAW" + b"\xff" * 200),
                       ("c.RAF", bytes(range(256)) * 10)):
        p = tmp_path / name
        p.write_bytes(data)
        assert focus.read(p) is None
    assert focus.read(tmp_path / "missing.RAF") is None
    assert focus.read(tmp_path / "x.DNG") is None
    assert focus.read(None) is None


def test_hostile_header_offsets(tmp_path):
    p = _raf(tmp_path / "h.RAF", _jpeg_with_exif(_tiff()))
    raw = bytearray(p.read_bytes())
    raw[0x54:0x5C] = struct.pack(">II", 0xFFFFFFF0, 0xFFFFFFFF)
    p.write_bytes(bytes(raw))
    assert focus.read(p) is None


def test_read_synthetic_raf(tmp_path):
    p = _raf(tmp_path / "s.RAF", _jpeg_with_exif(_tiff(pixel=(7, 8))))
    assert focus.read(p).point == (7, 8)


# ---- load(): flag gating ----------------------------------------------------------------------

def test_load_classic_does_not_parse(tmp_path, monkeypatch):
    p = _raf(tmp_path / "s.RAF", _jpeg_with_exif(_tiff()))
    calls = []
    monkeypatch.setattr(focus, "from_jpeg", lambda d: calls.append(1))
    monkeypatch.setattr(focus, "read", lambda d: calls.append(2))
    _im, info = preview.load(p)
    assert info.focus is None and calls == []


def test_load_advanced_sets_focus(tmp_path):
    p = _raf(tmp_path / "s.RAF", _jpeg_with_exif(_tiff(pixel=(7, 8))))
    features.set_mode(features.ADVANCED)
    _im, info = preview.load(p)
    assert info.source == "raf-embedded" and info.focus.point == (7, 8)


def test_load_advanced_non_raf_has_no_focus(tmp_path):
    features.set_mode(features.ADVANCED)
    p = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8)).save(p)
    _im, info = preview.load(p)
    assert info.focus is None


# ---- inspector --------------------------------------------------------------------------------

def _pic():
    from spuza.catalog import Picture

    p = Path("IMG_0001.RAF")
    return Picture("IMG_0001", p, None, (p,), "RAF", 1000, 0.0)


def test_inspector_line_and_classic_identical():
    fi = FocusInfo("X-T5", "Auto", "Single Point", (2207, 1472), (4416, 2944))
    base = PreviewInfo(Path("IMG_0001.RAF"), "raf-embedded", 10, 10, 1, 1.0)
    w0, w1 = Inspector(), Inspector()
    w0.set_picture(_pic(), base)
    w1.set_picture(_pic(), PreviewInfo(base.path, base.source, 10, 10, 1, 1.0, None, fi))
    assert "Focus" not in w0.plain()
    assert w1.plain().splitlines() == w0.plain().splitlines()[:-1] + [fi.describe(), "Not marked"]


# ---- real files -------------------------------------------------------------------------------

REAL = [
    (JES, "20250704_X-Pro2_9268.RAF", "X-Pro2", "Manual", None, (961, 641)),
    (SECOND_SET, "x100v.RAF", "X100V", "Auto", "Wide/Tracking", (1281, 1163)),
    (SECOND_SET, "xt4.RAF", "X-T4", "Auto", "Single Point", (2517, 1472)),
    (SECOND_SET, "xt50_small.RAF", "X-T50", "Auto", "Zone", (1591, 2086)),
    (SECOND_SET, "xt5_small.RAF", "X-T5", "Auto", "Single Point", (2207, 1472)),
]


@pytest.mark.parametrize("folder,name,model,mode,area,point", REAL)
def test_real_rafs_match_exiftool(folder, name, model, mode, area, point):
    p = folder / name
    if not p.is_file():
        pytest.skip(f"{p} not present")
    fi = focus.read(p)
    assert (fi.model, fi.mode, fi.area, fi.point, fi.orientation) == (model, mode, area, point, 1)
    if model == "X-Pro2":
        assert fi.frame == (1920, 1280) and not fi.usable() and fi.describe() == "Focus: manual"
    else:
        assert fi.frame == (4416, 2944) and fi.usable()
    data, _kind = preview.extract_embedded(p)
    assert focus.from_jpeg(data) == fi  # the bytes load() already holds give the same answer


def test_real_dng_has_none(real_set):
    for name in ("R0045146.DNG", "R0045147.DNG"):
        assert focus.read(real_set / name) is None


def test_real_load_flag_on_and_off(real_set):
    p = real_set / "20250704_X-Pro2_9268.RAF"
    _im, off = preview.load(p)
    assert off.focus is None
    features.set_mode(features.ADVANCED)
    _im, on = preview.load(p)
    assert on.focus.describe() == "Focus: manual"


def test_measure_read_ms(capsys):
    """No speed assertion: prints the median of 20 reads per real file for the report."""
    out = []
    for folder, name, *_ in REAL:
        p = folder / name
        if not p.is_file():
            continue
        ts = []
        for _ in range(20):
            t = time.perf_counter()
            focus.read(p)
            ts.append((time.perf_counter() - t) * 1000)
        out.append(f"{name}: {statistics.median(ts):.3f} ms")
    with capsys.disabled():
        print("\nfocus.read median-of-20 -> " + "; ".join(out))
