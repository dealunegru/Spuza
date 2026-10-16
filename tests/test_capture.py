"""Tests for spuza.capture: camera capture order from metadata, stdlib only.

The real jes set (read-only, skipped when absent) must order 146..150; the rest is synthetic bytes
built here, including truncated and hostile files, which must return None and never raise.
"""

import os
import random
import struct
import time

import pytest

from spuza import capture
from spuza.capture import CaptureKey, capture_key, sort_key

from conftest import DNG_NAMES, RAF_NAME, REAL_SET

DATE = b"2026:10:15 12:58:46"


def _ifd(bo, entries):
    """IFD bytes for [(tag, type, count, inline4_or_None)] at a fixed position: values must be inline."""
    out = struct.pack(bo + "H", len(entries))
    for tag, typ, cnt, inline in entries:
        out += struct.pack(bo + "HHI", tag, typ, cnt) + inline.ljust(4, b"\0")
    return out + b"\0\0\0\0"


def ricoh_mn(count, date=0x07EA0A0F, time_=0x0C3A2E, bo="<"):
    enc = count ^ date ^ (0xFFFFFFFF - (time_ << 8))  # the 3 time bytes are read as the top of a u32
    ents = [(0x0006, 7, 4, struct.pack(">I", date)),
            (0x0007, 7, 3, struct.pack(">I", time_)[1:]),
            (0x005D, 7, 4, struct.pack(">I", enc))]
    return b"RICOH\0" + (b"II" if bo == "<" else b"MM") + _ifd(bo, ents)


def fuji_mn(image_count=None, seq=None):
    ents = []
    if seq is not None:
        ents.append((0x1101, 4, 1, struct.pack("<I", seq)))
    if image_count is not None:
        ents.append((0x1438, 3, 1, struct.pack("<H", image_count)))
    return b"FUJIFILM" + struct.pack("<I", 12) + _ifd("<", ents)


def nikon_mn(count):
    inner = b"II" + struct.pack("<HI", 42, 8) + _ifd("<", [(0x00A7, 4, 1, struct.pack("<I", count))])
    return b"Nikon\0" + b"\x02\x10\0\0" + inner


def tiff(date=DATE, subsec=None, mn=None, bo="<", magic=42):
    """A TIFF: IFD0 -> ExifIFD with DateTimeOriginal [, SubSec] [, MakerNote]; values after the IFDs."""
    k = 1 + (subsec is not None) + (mn is not None)
    tail = 8 + 18 + 2 + 12 * k + 4
    vals = b""
    ents = []
    if date is not None:
        ents.append((0x9003, 2, len(date) + 1, struct.pack(bo + "I", tail + len(vals))))
        vals += date + b"\0"
    else:
        ents.append((0x9291, 2, 1, b"\0"))
    if subsec is not None and date is not None:
        s = subsec + b"\0"
        if len(s) <= 4:
            ents.append((0x9291, 2, len(s), s))
        else:
            ents.append((0x9291, 2, len(s), struct.pack(bo + "I", tail + len(vals))))
            vals += s
    elif subsec is not None:
        ents.append((0x9292, 2, 1, b"\0"))
    if mn is not None:
        ents.append((0x927C, 7, len(mn), struct.pack(bo + "I", tail + len(vals))))
        vals += mn
    ents.sort()
    ifd0 = _ifd(bo, [(0x8769, 4, 1, struct.pack(bo + "I", 26))])
    return (b"II" if bo == "<" else b"MM") + struct.pack(bo + "HI", magic, 8) + ifd0 + _ifd(bo, ents)[:-4] \
        + b"\0\0\0\0" + vals


def jpeg(tiff_bytes, pad=0):
    body = b"Exif\0\0" + tiff_bytes
    return b"\xff\xd8" + b"\xff\xe0\x00\x04JF" + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body \
        + b"\0" * pad + b"\xff\xd9"


def raf(jpeg_bytes):
    head = b"FUJIFILMCCD-RAW 0201".ljust(0x54, b" ") + struct.pack(">II", 0x100, len(jpeg_bytes))
    return head.ljust(0x100, b"\0") + jpeg_bytes


@pytest.fixture
def put(tmp_path):
    def _put(name, data):
        p = tmp_path / name
        p.write_bytes(data)
        return p
    return _put


# ---- the real set --------------------------------------------------------------------------------

@pytest.mark.skipif(not (REAL_SET / DNG_NAMES[0]).exists(), reason="real jes set not present")
def test_jes_orders_by_camera_not_mtime():
    names = [p.name for p in REAL_SET.iterdir() if p.suffix.lower() == ".dng"]
    keys = {n: capture_key(REAL_SET / n) for n in names}
    assert all(keys.values())
    assert sorted(names, key=lambda n: sort_key(keys[n], n)) == DNG_NAMES
    # two pairs share a second: the shutter counter, not mtime, breaks the tie
    assert keys["R0045146.DNG"].taken == keys["R0045147.DNG"].taken
    assert keys["R0045146.DNG"].counter + 1 == keys["R0045147.DNG"].counter


@pytest.mark.skipif(not (REAL_SET / RAF_NAME).exists(), reason="real jes set not present")
def test_jes_raf_has_fuji_key():
    k = capture_key(REAL_SET / RAF_NAME)
    assert k is not None and k.taken.startswith("2025-07-04T") and k.counter >= 0


# ---- ordering ------------------------------------------------------------------------------------

def test_subsec_orders_within_a_second(put):
    paths = {s: put(f"s{s.decode()}.jpg", jpeg(tiff(subsec=s))) for s in (b"5", b"25", b"05", b"3", b"500")}
    keys = {s: capture_key(p) for s, p in paths.items()}
    order = sorted(keys, key=lambda s: keys[s])
    assert order == [b"05", b"25", b"3", b"5", b"500"] or order == [b"05", b"25", b"3", b"500", b"5"]
    assert keys[b"5"] == keys[b"500"]  # 0.5 == 0.500
    assert keys[b"5"].subsec == "5"


def test_date_beats_counter_and_counter_breaks_ties(put):
    late = capture_key(put("a.dng", tiff(date=b"2026:10:15 12:58:47", mn=ricoh_mn(1))))
    early_hi = capture_key(put("b.dng", tiff(mn=ricoh_mn(900))))
    early_lo = capture_key(put("c.dng", tiff(mn=ricoh_mn(899))))
    assert early_lo < early_hi < late
    assert late.counter == 1 and early_hi.counter == 900


def test_ricoh_big_endian_makernote(put):
    k = capture_key(put("be.dng", tiff(bo=">", mn=ricoh_mn(66285, bo=">"))))
    assert k == CaptureKey("2026-10-15T12:58:46", "", 66285)


def test_fuji_image_count_masked_and_sequence_fallback(put):
    assert capture_key(put("f1.jpg", jpeg(tiff(mn=fuji_mn(image_count=0x8000 | 19035, seq=7))))).counter == 19035
    assert capture_key(put("f2.jpg", jpeg(tiff(mn=fuji_mn(seq=7))))).counter == 7


def test_nikon_shutter_count(put):
    assert capture_key(put("n.nef", tiff(mn=nikon_mn(123456)))).counter == 123456


def test_raf_reads_embedded_jpeg_exif(put):
    k = capture_key(put("x.raf", raf(jpeg(tiff(subsec=b"12", mn=fuji_mn(image_count=42))))))
    assert k == CaptureKey("2026-10-15T12:58:46", "12", 42)


def test_orf_style_tiff_magic(put):
    assert capture_key(put("o.orf", tiff(magic=0x4F52))) is not None


def test_missing_counter_sorts_first_at_same_instant(put):
    none = capture_key(put("n.jpg", jpeg(tiff())))
    one = capture_key(put("o.dng", tiff(mn=ricoh_mn(0))))
    assert none.counter == -1 and none < one


def test_sort_key_shape_and_unkeyed_last():
    a = sort_key(CaptureKey("2026-10-15T12:58:46", "", 5), "b.jpg")
    b = sort_key(CaptureKey("2026-10-15T12:58:46", "", 5), "a.jpg")
    none = sort_key(None, "a.jpg")
    assert b < a < none
    assert sorted([none, a, sort_key(CaptureKey("2020-01-01T00:00:00"), "z")])[0][1] == "2020-01-01T00:00:00"
    assert len(none) == len(a)


# ---- missing and hostile input -------------------------------------------------------------------

def test_no_date_or_no_exif_is_none(put):
    assert capture_key(put("nd.jpg", jpeg(tiff(date=None)))) is None
    assert capture_key(put("bare.jpg", b"\xff\xd8\xff\xd9")) is None
    assert capture_key(put("bad.jpg", jpeg(tiff(date=b"yesterday at noon")))) is None
    assert capture_key(put("txt.dng", b"hello world, not a picture")) is None


def test_unreadable_and_wrong_types_never_raise(tmp_path):
    assert capture_key(tmp_path / "nope.jpg") is None
    assert capture_key(tmp_path) is None
    assert capture_key(tmp_path / ("x" * 5000)) is None
    for bad in (None, 7, float("nan"), b"\0", object()):
        assert capture_key(bad) is None
    (tmp_path / "empty.jpg").write_bytes(b"")
    assert capture_key(tmp_path / "empty.jpg") is None


def test_every_truncation_is_clean(put):
    data = raf(jpeg(tiff(subsec=b"123456", mn=ricoh_mn(5))))
    full = capture_key(put("full.raf", data))
    for cut in range(0, len(data), 3):
        k = capture_key(put("cut.raf", data[:cut]))
        assert k is None or k == full or k.counter in (-1, 5)
    for kind, blob in (("jpg", jpeg(tiff(mn=ricoh_mn(5)))), ("dng", tiff(mn=ricoh_mn(5)))):
        for cut in range(0, len(blob)):
            capture_key(put(f"cut.{kind}", blob[:cut]))


def test_cyclic_exif_pointer_is_none(put):
    data = bytearray(tiff())
    data[8 + 2 + 8:8 + 2 + 12] = struct.pack("<I", 8)  # ExifIFD pointer back at IFD0
    assert capture_key(put("cyc.dng", bytes(data))) is None


def test_huge_counts_and_entry_numbers(put):
    t = bytearray(tiff(mn=ricoh_mn(5)))
    # DateTimeOriginal claims 4 GB: nothing is read, no memory blown, a clean None
    exif_ents = 26 + 2
    for i in range(3):
        if struct.unpack_from("<H", t, exif_ents + 12 * i)[0] == 0x9003:
            struct.pack_into("<I", t, exif_ents + 12 * i + 4, 0xFFFFFFFF)
    assert capture_key(put("big.dng", bytes(t))) is None
    t2 = bytearray(tiff())
    struct.pack_into("<H", t2, 26, 0xFFFF)  # 65535 entries in the Exif IFD
    assert capture_key(put("many.dng", bytes(t2))) is None
    t3 = bytearray(tiff())
    struct.pack_into("<I", t3, 4, 0xFFFFFFF0)  # IFD0 offset far outside the file
    assert capture_key(put("far.dng", bytes(t3))) is None


def test_makernote_pointing_at_itself_or_garbage(put):
    junk = b"RICOH\0II" + b"\xff" * 64
    assert capture_key(put("j.dng", tiff(mn=junk))).counter == -1
    loop = b"FUJIFILM" + struct.pack("<I", 0)  # IFD offset 0: reads the header as an IFD
    assert capture_key(put("l.dng", tiff(mn=loop))).counter == -1


def test_jpeg_segment_storm_is_bounded(put):
    data = b"\xff\xd8" + b"\xff\xe0\x00\x02" * 100000 + b"\xff\xd9"
    t0 = time.perf_counter()
    assert capture_key(put("storm.jpg", data)) is None
    assert time.perf_counter() - t0 < 5  # generous: a hang, not a timing, is what this catches


def test_random_bytes_never_raise(put):
    rnd = random.Random(1234)
    seeds = [jpeg(tiff(mn=ricoh_mn(9))), tiff(mn=fuji_mn(image_count=3)), raf(jpeg(tiff()))]
    for base in seeds:
        for _ in range(150):
            b = bytearray(base)
            for _ in range(rnd.randint(1, 6)):
                b[rnd.randrange(len(b))] = rnd.randrange(256)
            capture_key(put("fuzz.bin", bytes(b)))
    for _ in range(50):
        capture_key(put("rnd.bin", bytes(rnd.randrange(256) for _ in range(rnd.randint(0, 600)))))


def test_read_budget_is_enforced(put, monkeypatch):
    monkeypatch.setattr(capture, "MAX_READS", 2)
    assert capture_key(put("tiny.dng", tiff(mn=ricoh_mn(5)))) is None


def test_stdlib_only():
    src = open(capture.__file__, encoding="utf-8").read()
    assert "PIL" not in src and "PySide6" not in src


# ---- timing: reported, never asserted tightly ----------------------------------------------------

@pytest.mark.skipif(not REAL_SET.exists(), reason="real jes set not present")
def test_timing_report_on_real_set(capsys):
    files = [p for p in REAL_SET.iterdir() if p.is_file()]
    t0 = time.perf_counter()
    reps = 20
    for _ in range(reps):
        for p in files:
            capture_key(p)
    ms = (time.perf_counter() - t0) / (reps * len(files)) * 1000
    with capsys.disabled():
        print(f"\ncapture_key: {ms:.3f} ms/file over {len(files)} files x {reps}")
    assert os.path.isdir(REAL_SET)
