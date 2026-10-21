"""XMP pill store (spuza.xmp): lossless embed in JPEG/PNG, sidecar, merge, read-back. Synthetic data only."""

from __future__ import annotations

import io
import os
import struct
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from spuza import xmp
from spuza.xmp import Outcome, PillTag, build_packet, parse_pills

STD = b"http://ns.adobe.com/xap/1.0/\x00"
EXT = b"http://ns.adobe.com/xmp/extension/\x00"
CLIENT = PillTag("Client", "#ff4d4d")
PRINT = PillTag("Print", "#2fb4e8")


def foreign_packet(extra_subject=("Holiday", "Client"), label="Purple", old_pills=None) -> bytes:
    pills = ""
    if old_pills:
        items = "".join(
            f'<rdf:li rdf:parseType="Resource"><spuza:name>{n}</spuza:name><spuza:hex>{h}</spuza:hex></rdf:li>'
            for n, h in old_pills
        )
        pills = f"<spuza:pills><rdf:Seq>{items}</rdf:Seq></spuza:pills>"
    subj = "".join(f"<rdf:li>{s}</rdf:li>" for s in extra_subject)
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:spuza="http://spuza.app/ns/1.0/"'
        ' xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" photoshop:City="Cluj">'
        f"<dc:subject><rdf:Bag>{subj}</rdf:Bag></dc:subject><xmp:Label>{label}</xmp:Label>"
        f"<xmp:Rating>4</xmp:Rating><dc:creator><rdf:Seq><rdf:li>Daniel</rdf:li></rdf:Seq></dc:creator>{pills}"
        "</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end=\"w\"?>"
    ).encode("utf-8")


def seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def make_jpeg(size=(64, 48), color=(200, 30, 40), noise=False) -> bytes:
    im = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)) if noise else Image.new("RGB", size, color)
    if not noise:
        for x in range(size[0]):
            im.putpixel((x, x % size[1]), (x, 255 - x, 7))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def after_app0(data: bytes) -> int:
    assert data[2:4] == b"\xff\xe0"
    return 4 + struct.unpack(">H", data[4:6])[0]


def with_segments(data: bytes, *segs: bytes) -> bytes:
    at = after_app0(data)
    return data[:at] + b"".join(segs) + data[at:]


def pixels(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as im:
        return im.tobytes()


def jpeg_markers(data: bytes) -> list[tuple[int, bytes]]:
    out, i = [], 2
    while data[i + 1] != 0xDA:
        n = struct.unpack(">H", data[i + 2 : i + 4])[0]
        out.append((data[i + 1], data[i + 4 : i + 2 + n]))
        i += 2 + n
    return out


def outside(data: bytes, payload_marker_prefix: bytes) -> bytes:
    """data with the one APP1 segment starting `prefix` cut out."""
    i = 2
    while data[i + 1] != 0xDA:
        n = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if data[i + 1] == 0xE1 and data[i + 4 : i + 4 + len(payload_marker_prefix)] == payload_marker_prefix:
            return data[:i] + data[i + 2 + n :]
        i += 2 + n
    return data


def packet_of(data: bytes) -> bytes:
    got = [p for m, p in jpeg_markers(data) if m == 0xE1 and p.startswith(STD)]
    assert len(got) == 1
    return got[0][len(STD) :]


def make_png(size=(40, 30)) -> bytes:
    im = Image.new("RGB", size, (10, 120, 200))
    im.putpixel((3, 4), (255, 0, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def png_chunks(data: bytes) -> list[tuple[bytes, bytes, int]]:
    out, i = [], 8
    while i < len(data):
        n = struct.unpack(">I", data[i : i + 4])[0]
        typ, body = data[i + 4 : i + 8], data[i + 8 : i + 8 + n]
        (crc,) = struct.unpack(">I", data[i + 8 + n : i + 12 + n])
        assert crc == zlib.crc32(typ + body), typ
        out.append((typ, body, crc))
        i += 12 + n
    return out


# ---------------------------------------------------------------- packet --------------------------------------------


def test_build_packet_fresh_roundtrip():
    pk = build_packet([CLIENT, PRINT])
    assert pk.startswith(b"<?xpacket begin=") and pk.rstrip().endswith(b'<?xpacket end="w"?>')
    assert parse_pills(pk) == [CLIENT, PRINT]
    assert b"<rdf:li>Client</rdf:li>" in pk and b"<xmp:Label>Red</xmp:Label>" in pk


def test_build_packet_merge_keeps_foreign_content():
    old = foreign_packet(label="Purple")
    pk = build_packet([CLIENT, PRINT], old)
    text = pk.decode()
    assert "Holiday" in text and "Cluj" in text and "Daniel" in text and "<xmp:Rating>4</xmp:Rating>" in text
    assert "<xmp:Label>Purple</xmp:Label>" in text  # foreign label never replaced
    assert text.count("Client") >= 2  # the pill name is in the bag and in dc:subject, but "Client" keyword not doubled
    assert text.count("<rdf:li>Client</rdf:li>") == 1
    assert parse_pills(pk) == [CLIENT, PRINT]
    assert parse_pills(old) is None  # plain dc:subject keywords are not pills


def test_label_follows_own_value_only():
    first = build_packet([CLIENT])  # Red written by us, recorded
    second = build_packet([PillTag("Sky", "#2fb4e8")], first)
    assert b"<xmp:Label>Blue</xmp:Label>" in second and b"Red" not in second
    # someone else changes the label: we must not touch it afterwards
    edited = first.replace(b"<xmp:Label>Red</xmp:Label>", b"<xmp:Label>Yellow</xmp:Label>")
    third = build_packet([PillTag("Sky", "#2fb4e8")], edited)
    assert b"<xmp:Label>Yellow</xmp:Label>" in third and b"Blue" not in third
    # removing our pills must not remove a label that is no longer ours
    fourth = build_packet([], edited)
    assert b"<xmp:Label>Yellow</xmp:Label>" in fourth


def test_colour_name_is_nearest_swatch():
    for hexv, name in [("#ff4d4d", "Red"), ("#8fd14f", "Green"), ("#f2d12e", "Yellow"), ("#fe8a20", "Orange"),
                       ("#101010", "Gray"), ("#ffffff", "None")]:
        assert f"<xmp:Label>{name}</xmp:Label>".encode() in build_packet([PillTag("a", hexv)]), hexv


def test_subject_loses_only_old_pill_names():
    old = foreign_packet(extra_subject=("Holiday", "Client", "Gone"), old_pills=[("Client", "#ff4d4d"), ("Gone", "#8e8e96")])
    pk = build_packet([PRINT], old)
    text = pk.decode()
    assert "<rdf:li>Holiday</rdf:li>" in text and "<rdf:li>Print</rdf:li>" in text
    assert "<rdf:li>Client</rdf:li>" not in text and "<rdf:li>Gone</rdf:li>" not in text
    assert parse_pills(pk) == [PRINT]


def test_empty_pills_removes_only_own_entries():
    on = build_packet([CLIENT, PRINT], foreign_packet(label="Purple"))
    off = build_packet([], on)
    text = off.decode()
    assert parse_pills(off) is None
    assert "Holiday" in text and "Cluj" in text and "<xmp:Rating>4</xmp:Rating>" in text
    assert "<rdf:li>Print</rdf:li>" not in text and "spuza:pills" not in text
    clean = build_packet([CLIENT, PRINT])
    assert parse_pills(build_packet([], clean)) is None
    assert b"<rdf:li>Client</rdf:li>" not in build_packet([], clean)


def test_build_packet_bad_input():
    for bad in (b"", b"not xml", b"<a><b></a>", b"\xff\xfe\x00", b"<!DOCTYPE x [<!ENTITY a 'b'>]><x>&a;</x>"):
        with pytest.raises(ValueError):
            build_packet([CLIENT], bad)
    deep = b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>" + b"<a>" * 5000 + b"</a>" * 5000 + b"</x:xmpmeta>"
    with pytest.raises(ValueError):
        build_packet([CLIENT], deep)
    assert parse_pills(b"") is None and parse_pills(b"junk") is None and parse_pills(deep) is None
    assert parse_pills(None) is None  # type: ignore[arg-type]
    assert parse_pills(12) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------- JPEG ----------------------------------------------


def test_jpeg_insert_lossless_after_app0_and_exif():
    base = make_jpeg()
    ex = Image.Exif()
    ex[0x010E] = "synthetic"
    exif = seg(0xE1, ex.tobytes())
    for data, expect in ((base, [0xE0, 0xE1]), (with_segments(base, exif), [0xE0, 0xE1, 0xE1])):
        new, reason = xmp.embed(data, ".jpg", [CLIENT, PRINT])
        assert new is not None, reason
        assert parse_pills(packet_of(new)) == [CLIENT, PRINT]
        assert outside(new, STD) == data  # every byte outside the XMP segment is the original
        assert pixels(new) == pixels(data)
        marks = [m for m, _ in jpeg_markers(new)]
        assert marks[: len(expect)] == expect
        assert jpeg_markers(new)[len(expect) - 1][1].startswith(STD)  # right after the APP0/Exif run


def test_jpeg_scan_reaches_xmp_after_icc_app2_and_merges_in_place():
    base = make_jpeg()
    icc = seg(0xE2, b"ICC_PROFILE\x00\x01\x01" + bytes(300))
    app13 = seg(0xED, b"Photoshop 3.0\x00" + bytes(40))
    old = foreign_packet(label="Purple")
    data = with_segments(base, icc, app13, seg(0xE1, STD + old))
    new, reason = xmp.embed(data, ".jpg", [CLIENT])
    assert new is not None, reason
    pk = packet_of(new)  # exactly one standard packet, so no second one was added
    assert parse_pills(pk) == [CLIENT] and b"Holiday" in pk and b"Cluj" in pk and b"<xmp:Label>Purple" in pk
    assert outside(new, STD) == outside(data, STD)
    # the rewritten segment is at the same place in the stream
    assert [m for m, _ in jpeg_markers(new)] == [m for m, _ in jpeg_markers(data)]
    assert pixels(new) == pixels(data)


def test_jpeg_declines_second_standard_extended_and_malformed():
    base = make_jpeg()
    one = seg(0xE1, STD + foreign_packet())
    cases = {
        "second": with_segments(base, one, seg(0xE2, b"x"), one),
        "extended": with_segments(base, one, seg(0xE1, EXT + bytes(60))),
        "extended-only": with_segments(base, seg(0xE1, EXT + bytes(60))),
    }
    for name, data in cases.items():
        new, reason = xmp.embed(data, ".jpg", [CLIENT])
        assert new is None and reason, name
    bad_len = base[:2] + b"\xff\xe5\xff\xf0abc" + base[2:]  # length runs past the end
    assert xmp.embed(bad_len, ".jpg", [CLIENT])[0] is None
    no_sos = base[: base.index(b"\xff\xda")]
    assert xmp.embed(no_sos, ".jpg", [CLIENT])[0] is None
    assert xmp.embed(b"\xff\xd8", ".jpg", [CLIENT])[0] is None
    assert xmp.embed(b"", ".jpg", [CLIENT])[0] is None
    assert xmp.embed(b"GIF89a", ".gif", [CLIENT])[0] is None
    assert xmp.embed(base[:2] + b"\x00\x00" + base[4:], ".jpeg", [CLIENT])[0] is None
    # a packet that cannot fit one segment
    big = [PillTag("p" * 200 + str(i), "#ff4d4d") for i in range(400)]
    new, reason = xmp.embed(base, ".jpg", big)
    assert new is None and "65533" in reason
    # an unparseable existing packet
    junk = with_segments(base, seg(0xE1, STD + b"<oops"))
    assert xmp.embed(junk, ".jpg", [CLIENT])[0] is None


def test_jpeg_empty_pills_without_xmp_is_unchanged():
    base = make_jpeg()
    new, _ = xmp.embed(base, ".jpg", [])
    assert new == base


def test_apply_embedded_writes_and_verifies(tmp_path: Path):
    data = with_segments(make_jpeg(), seg(0xE1, b"Exif\x00\x00" + bytes(30)))
    tmp = tmp_path / "a.jpg"
    tmp.write_bytes(data)
    out = xmp.apply_embedded(tmp, [CLIENT])
    assert out == Outcome("written")
    assert parse_pills(xmp.read_embedded(tmp)) == [CLIENT]
    assert outside(tmp.read_bytes(), STD) == data and pixels(tmp.read_bytes()) == pixels(data)
    assert [p.name for p in tmp_path.iterdir()] == ["a.jpg"]  # sibling temp removed


def test_apply_embedded_restores_on_forced_verify_failure(tmp_path: Path, monkeypatch):
    data = make_jpeg()
    tmp = tmp_path / "a.jpg"
    tmp.write_bytes(data)
    monkeypatch.setattr(xmp, "_verify", lambda old, new, ext: "forced")
    out = xmp.apply_embedded(tmp, [CLIENT])
    assert out.status == "declined" and "forced" in out.reason
    assert tmp.read_bytes() == data
    assert [p.name for p in tmp_path.iterdir()] == ["a.jpg"]


def test_apply_embedded_restores_when_verify_raises_and_never_raises(tmp_path: Path, monkeypatch):
    data = make_jpeg()
    tmp = tmp_path / "a.jpg"
    tmp.write_bytes(data)

    def boom(old, new, ext):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(xmp, "_verify", boom)
    out = xmp.apply_embedded(tmp, [CLIENT])
    assert out.status == "declined" and tmp.read_bytes() == data
    monkeypatch.undo()
    assert xmp.apply_embedded(tmp_path / "missing.jpg", [CLIENT]).status == "declined"
    raf = tmp_path / "x.raf"
    raf.write_bytes(os.urandom(500))
    assert xmp.apply_embedded(raf, [CLIENT]).status == "declined"
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8garbage")
    assert xmp.apply_embedded(bad, [CLIENT]).status == "declined" and bad.read_bytes() == b"\xff\xd8garbage"


def test_apply_embedded_declines_and_keeps_data_for_second_packet(tmp_path: Path):
    one = seg(0xE1, STD + foreign_packet())
    data = with_segments(make_jpeg(), one, one)
    tmp = tmp_path / "a.jpg"
    tmp.write_bytes(data)
    assert xmp.apply_embedded(tmp, [CLIENT]).status == "declined"
    assert tmp.read_bytes() == data


def test_verify_detects_changed_pixels_and_changed_bytes():
    a = make_jpeg(color=(1, 2, 3))
    b = make_jpeg(color=(9, 9, 9))
    good, _ = xmp.embed(a, ".jpg", [CLIENT])
    assert xmp._verify(a, good, ".jpg") == ""
    assert xmp._verify(a, b, ".jpg") != ""
    tweaked = bytearray(good)
    tweaked[-5] ^= 0x01
    assert xmp._verify(a, bytes(tweaked), ".jpg") != ""


# ---------------------------------------------------------------- PNG -----------------------------------------------


def test_png_itxt_roundtrip_crc_and_pixels(tmp_path: Path):
    data = make_png()
    new, reason = xmp.embed(data, ".png", [CLIENT, PRINT])
    assert new is not None, reason
    chunks = png_chunks(new)  # asserts every CRC
    kinds = [c[0] for c in chunks]
    assert kinds.index(b"iTXt") < kinds.index(b"IDAT")
    assert pixels(new) == pixels(data)
    p = tmp_path / "a.png"
    p.write_bytes(new)
    assert parse_pills(xmp.read_embedded(p)) == [CLIENT, PRINT]
    # merge into the existing chunk: still exactly one
    again, reason = xmp.embed(new, ".png", [PRINT])
    assert again is not None, reason
    assert [c[0] for c in png_chunks(again)].count(b"iTXt") == 1
    p.write_bytes(again)
    assert parse_pills(xmp.read_embedded(p)) == [PRINT]
    assert [c for c in png_chunks(again) if c[0] != b"iTXt"] == [c for c in png_chunks(data) if c[0] != b"iTXt"]


def test_png_merge_with_foreign_itxt_and_odd_files_declined(tmp_path: Path):
    data = make_png()
    ihdr_end = 8 + 12 + 13
    payload = b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + foreign_packet()
    chunk = struct.pack(">I", len(payload)) + b"iTXt" + payload + struct.pack(">I", zlib.crc32(b"iTXt" + payload))
    withx = data[:ihdr_end] + chunk + data[ihdr_end:]
    new, reason = xmp.embed(withx, ".png", [CLIENT])
    assert new is not None, reason
    text = new.decode("latin-1")
    assert "Holiday" in text and "Cluj" in text
    assert xmp.embed(data[:ihdr_end] + chunk + chunk + data[ihdr_end:], ".png", [CLIENT])[0] is None  # two packets
    no_idat = data[:ihdr_end]
    assert xmp.embed(no_idat, ".png", [CLIENT])[0] is None
    assert xmp.embed(data[:30], ".png", [CLIENT])[0] is None
    assert xmp.embed(b"\x89PNG\r\n\x1a\n" + b"\xff\xff\xff\xffIHDR", ".png", [CLIENT])[0] is None
    assert xmp.embed(b"junk", ".png", [CLIENT])[0] is None
    p = tmp_path / "a.png"
    p.write_bytes(data)
    assert xmp.apply_embedded(p, [CLIENT]).status == "written"
    assert pixels(p.read_bytes()) == pixels(data)


# ---------------------------------------------------------------- sidecar -------------------------------------------


def test_sidecar_create_and_temp_removed(tmp_path: Path):
    sc = tmp_path / "DSCF0001.xmp"
    assert xmp.write_sidecar(sc, [CLIENT, PRINT]) == Outcome("written")
    assert parse_pills(xmp.read_sidecar(sc)) == [CLIENT, PRINT]
    assert [p.name for p in tmp_path.iterdir()] == ["DSCF0001.xmp"]


def test_sidecar_merges_into_foreign_sidecar(tmp_path: Path):
    sc = tmp_path / "IMG.xmp"
    sc.write_bytes(foreign_packet(label="Purple"))
    assert xmp.write_sidecar(sc, [CLIENT]).status == "written"
    text = sc.read_text("utf-8")
    assert "Holiday" in text and "Cluj" in text and "Daniel" in text and "<xmp:Label>Purple" in text
    assert parse_pills(sc.read_bytes()) == [CLIENT]
    assert [p.name for p in tmp_path.iterdir()] == ["IMG.xmp"]
    assert xmp.write_sidecar(sc, []).status == "written"
    assert parse_pills(sc.read_bytes()) is None and "Holiday" in sc.read_text("utf-8")


def test_sidecar_never_clobbers_unparseable_or_raced_file(tmp_path: Path, monkeypatch):
    sc = tmp_path / "IMG.xmp"
    sc.write_bytes(b"<not xml at all")
    out = xmp.write_sidecar(sc, [CLIENT])
    assert out.status == "declined" and out.reason
    assert sc.read_bytes() == b"<not xml at all"
    assert [p.name for p in tmp_path.iterdir()] == ["IMG.xmp"]
    # another tool publishes the sidecar between our check and our link
    sc2 = tmp_path / "RACE.xmp"
    real_link = os.link

    def racing_link(src, dst, *a, **k):
        Path(dst).write_bytes(foreign_packet())
        return real_link(src, dst, *a, **k)  # raises FileExistsError

    monkeypatch.setattr(xmp.os, "link", racing_link)
    assert xmp.write_sidecar(sc2, [CLIENT]).status == "written"
    monkeypatch.undo()
    assert "Holiday" in sc2.read_text("utf-8") and parse_pills(sc2.read_bytes()) == [CLIENT]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["IMG.xmp", "RACE.xmp"]


def test_sidecar_without_hardlinks_still_creates_exclusively(tmp_path: Path, monkeypatch):
    def no_link(*a, **k):
        raise OSError("not supported")

    monkeypatch.setattr(xmp.os, "link", no_link)
    sc = tmp_path / "A.xmp"
    assert xmp.write_sidecar(sc, [CLIENT]).status == "written"
    monkeypatch.undo()
    assert parse_pills(sc.read_bytes()) == [CLIENT]
    assert [p.name for p in tmp_path.iterdir()] == ["A.xmp"]


def test_sidecar_verify_failure_keeps_old_file(tmp_path: Path, monkeypatch):
    sc = tmp_path / "IMG.xmp"
    old = foreign_packet()
    sc.write_bytes(old)
    monkeypatch.setattr(xmp, "build_packet", lambda pills, existing=None: build_packet([PRINT], existing))
    out = xmp.write_sidecar(sc, [CLIENT])
    assert out.status == "declined" and sc.read_bytes() == old
    assert [p.name for p in tmp_path.iterdir()] == ["IMG.xmp"]


def test_write_sidecar_missing_directory_declines(tmp_path: Path):
    assert xmp.write_sidecar(tmp_path / "nope" / "A.xmp", [CLIENT]).status == "declined"


def test_raw_is_byte_identical_after_sidecar(tmp_path: Path):
    raf = tmp_path / "DSCF0001.raf"
    blob = os.urandom(200_000)
    raf.write_bytes(blob)
    assert xmp.write_sidecar(tmp_path / "DSCF0001.xmp", [CLIENT]).status == "written"
    assert raf.read_bytes() == blob
    assert sorted(p.name for p in tmp_path.iterdir()) == ["DSCF0001.raf", "DSCF0001.xmp"]


# ---------------------------------------------------------------- read-back -----------------------------------------


def test_read_embedded_and_sidecar_edge_cases(tmp_path: Path):
    assert xmp.read_embedded(tmp_path / "missing.jpg") is None
    assert xmp.read_sidecar(tmp_path / "missing.xmp") is None
    junk = tmp_path / "j.jpg"
    junk.write_bytes(b"\xff\xd8\xff\xe1\xff\xffshort")
    assert xmp.read_embedded(junk) is None
    plain = tmp_path / "p.jpg"
    plain.write_bytes(make_jpeg())
    assert xmp.read_embedded(plain) is None
    assert xmp.read_embedded(tmp_path) is None  # a directory
    raf = tmp_path / "r.raf"
    raf.write_bytes(os.urandom(100))
    assert xmp.read_embedded(raf) is None


def test_read_embedded_stops_at_sos_without_reading_the_scan(tmp_path: Path, monkeypatch):
    data = with_segments(make_jpeg(), seg(0xE1, STD + build_packet([CLIENT])))
    big = data + bytes(3_000_000)
    p = tmp_path / "a.jpg"
    p.write_bytes(big)
    reads: list[int] = []
    real_open = io.open

    class Spy:
        def __init__(self, f):
            self.f = f

        def read(self, n=-1):
            reads.append(n)
            return self.f.read(n)

        def __getattr__(self, k):
            return getattr(self.f, k)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

    monkeypatch.setattr(xmp, "open", lambda *a, **k: Spy(real_open(*a, **k)), raising=False)
    assert parse_pills(xmp.read_embedded(p)) == [CLIENT]
    assert reads and all(0 <= n < 70_000 for n in reads)  # never an unbounded read()


def pic(primary: Path, sidecar: Path | None = None, companions=()):
    return SimpleNamespace(stem=primary.stem, primary=primary, sidecar=sidecar, companions=tuple(companions))


def test_read_picture_pills_raw_primary_prefers_sidecar_over_twin(tmp_path: Path):
    raf = tmp_path / "A.raf"
    raf.write_bytes(os.urandom(100))
    jpg = tmp_path / "A.jpg"
    jpg.write_bytes(make_jpeg())
    side = tmp_path / "A.XMP"
    assert xmp.apply_embedded(jpg, [PRINT]).status == "written"
    assert xmp.write_sidecar(side, [CLIENT]).status == "written"
    assert xmp.read_picture_pills(pic(raf, jpg, [side])) == [CLIENT]  # disagree: the RAW's sidecar wins
    assert xmp.write_sidecar(side, []).status == "written"  # a pill removed there stays removed: no union
    assert xmp.read_picture_pills(pic(raf, jpg, [side])) == [PRINT]  # sidecar has no bag -> twin's embedded
    assert xmp.read_picture_pills(pic(raf, None, [])) is None


def test_read_picture_pills_jpeg_primary_prefers_embedded(tmp_path: Path):
    jpg = tmp_path / "B.jpg"
    jpg.write_bytes(make_jpeg())
    side = tmp_path / "B.xmp"
    xmp.apply_embedded(jpg, [PRINT])
    xmp.write_sidecar(side, [CLIENT])
    assert xmp.read_picture_pills(pic(jpg, None, [side])) == [PRINT]
    plain = tmp_path / "C.jpg"
    plain.write_bytes(make_jpeg())
    side2 = tmp_path / "C.xmp"
    xmp.write_sidecar(side2, [CLIENT])
    assert xmp.read_picture_pills(pic(plain, None, [side2])) == [CLIENT]  # embedded has no bag -> its sidecar


def test_read_picture_pills_ignores_plain_keywords_and_never_raises(tmp_path: Path):
    jpg = tmp_path / "D.jpg"
    jpg.write_bytes(with_segments(make_jpeg(), seg(0xE1, STD + foreign_packet())))
    side = tmp_path / "D.xmp"
    side.write_bytes(foreign_packet())
    assert xmp.read_picture_pills(pic(jpg, None, [side])) is None
    gone = tmp_path / "gone.xmp"
    assert xmp.read_picture_pills(pic(tmp_path / "E.raf", tmp_path / "E.jpg", [gone])) is None
    assert xmp.read_picture_pills(pic(tmp_path / "F.raf", None, [tmp_path])) is None


def test_embed_timing_5mb_jpeg(capsys):
    data = make_jpeg(size=(1700, 1200), noise=True)
    assert len(data) > 1_000_000
    data = data + bytes(max(0, 5_000_000 - len(data)))  # trailing padding after EOI keeps it ~5 MB
    best = []
    for _ in range(5):
        t0 = time.perf_counter()
        new, reason = xmp.embed(data, ".jpg", [CLIENT, PRINT])
        best.append((time.perf_counter() - t0) * 1000)
        assert new is not None, reason
    with capsys.disabled():
        print(f"\nembed of a {len(data) / 1e6:.1f} MB JPEG: min {min(best):.1f} ms, max {max(best):.1f} ms")


# ---------------------------------------------------------------- review round 2 ------------------------------------


NOOP = Outcome("written", "nothing to change")


def test_no_op_when_nothing_of_ours_exists_or_already_equal(tmp_path: Path):
    foreign = foreign_packet(label="Purple")
    data = with_segments(make_jpeg(), seg(0xE1, STD + foreign))
    assert xmp.embed(data, ".jpg", [])[0] is data
    first, _ = xmp.embed(data, ".jpg", [CLIENT, PRINT])
    assert first is not None and first != data
    again, _ = xmp.embed(first, ".jpg", [CLIENT, PRINT])
    assert again is first  # same pills again: no re-serialisation
    png = make_png()
    ihdr_end = 8 + 12 + 13
    pl = _PNG_KEY + b"\x00\x00\x00\x00" + foreign
    ch = struct.pack(">I", len(pl)) + b"iTXt" + pl + struct.pack(">I", zlib.crc32(b"iTXt" + pl))
    pdata = png[:ihdr_end] + ch + png[ihdr_end:]
    assert xmp.embed(pdata, ".png", [])[0] is pdata
    tmp = tmp_path / "a.jpg"
    tmp.write_bytes(data)
    assert xmp.apply_embedded(tmp, []) == NOOP and tmp.read_bytes() == data
    tmp.write_bytes(first)
    assert xmp.apply_embedded(tmp, [CLIENT, PRINT]) == NOOP and tmp.read_bytes() == first
    sc = tmp_path / "A.xmp"
    sc.write_bytes(foreign)
    ns = sc.stat().st_mtime_ns
    assert xmp.write_sidecar(sc, []) == NOOP
    assert sc.read_bytes() == foreign and sc.stat().st_mtime_ns == ns
    assert xmp.write_sidecar(sc, [CLIENT]).status == "written"
    written = sc.read_bytes()
    assert xmp.write_sidecar(sc, [CLIENT]) == NOOP and sc.read_bytes() == written
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


_PNG_KEY = b"XML:com.adobe.xmp\x00"


def test_xb_fallback_removes_truncated_file_on_write_failure(tmp_path: Path, monkeypatch):
    def no_link(*a, **k):
        raise OSError("not supported")

    real_open = io.open

    class Boom:
        def __init__(self, f):
            self.f = f

        def write(self, b):
            self.f.write(b[:5])
            raise OSError("disk full")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

    def fake_open(path, mode="r", *a, **k):
        f = real_open(path, mode, *a, **k)
        return Boom(f) if mode == "xb" else f

    monkeypatch.setattr(xmp.os, "link", no_link)
    monkeypatch.setattr(xmp, "open", fake_open, raising=False)
    sc = tmp_path / "A.xmp"
    out = xmp.write_sidecar(sc, [CLIENT])
    assert out.status == "declined" and not sc.exists()
    assert list(tmp_path.iterdir()) == []


def test_temp_names_are_unique_per_writer(tmp_path: Path, monkeypatch):
    seen: list[str] = []
    real_link, real_replace = os.link, os.replace

    def link(src, dst, *a, **k):
        seen.append(Path(src).name)
        return real_link(src, dst, *a, **k)

    def replace(src, dst, *a, **k):
        seen.append(Path(src).name)
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(xmp.os, "link", link)
    monkeypatch.setattr(xmp.os, "replace", replace)
    sc = tmp_path / "A.xmp"
    assert xmp.write_sidecar(sc, [CLIENT]).status == "written"
    assert xmp.write_sidecar(sc, [PRINT]).status == "written"
    assert xmp.write_sidecar(tmp_path / "B.xmp", [CLIENT]).status == "written"
    assert len(seen) == 3 and len(set(seen)) == 3
    assert all(n.startswith(".") and n.endswith(".spuza-part") and str(os.getpid()) in n for n in seen)


@pytest.mark.parametrize("how", ["size", "mtime"])
def test_merge_declines_when_sidecar_changed_meanwhile(tmp_path: Path, monkeypatch, how):
    sc = tmp_path / "IMG.xmp"
    old = foreign_packet()
    sc.write_bytes(old)
    st = sc.stat()
    real_build = xmp.build_packet

    def build(pills, existing=None):
        out = real_build(pills, existing)
        if how == "size":
            sc.write_bytes(old + b" ")
        else:
            os.utime(sc, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        return out

    monkeypatch.setattr(xmp, "build_packet", build)
    out = xmp.write_sidecar(sc, [CLIENT])
    assert out.status == "declined" and "changed" in out.reason
    assert parse_pills(sc.read_bytes()) is None
    assert [p.name for p in tmp_path.iterdir()] == ["IMG.xmp"]


def test_walker_declines_fill_bytes_and_standalone_markers_before_sos():
    base = make_jpeg()
    for junk in (b"\xff", b"\xff\xd0", b"\xff\x01", b"\xff\xd9"):
        data = base[:2] + junk + base[2:]
        new, reason = xmp.embed(data, ".jpg", [CLIENT])
        assert new is None and reason, junk


def test_two_description_foreign_packet_gets_one_subject():
    two = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"><xmp:Rating>3</xmp:Rating>'
        "</rdf:Description>"
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:subject><rdf:Bag><rdf:li>Holiday</rdf:li></rdf:Bag></dc:subject></rdf:Description>"
        "</rdf:RDF></x:xmpmeta>"
    ).encode()
    pk = build_packet([CLIENT], two).decode()
    assert pk.count("<dc:subject>") == 1 and "<rdf:li>Holiday</rdf:li>" in pk and "<rdf:li>Client</rdf:li>" in pk
    assert "<xmp:Rating>3</xmp:Rating>" in pk
    assert parse_pills(pk.encode()) == [CLIENT]
