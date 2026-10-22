"""Star ratings, data layer (task sz-stars-core): xmp:Rating beside spuza:pills, and the per-app index's stars.

Synthetic data only (tiny Pillow images in tmp_path)."""

from __future__ import annotations

import io
import json
import re
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from spuza import pills as pills_mod
from spuza import xmp
from spuza.xmp import PillTag

CLIENT = PillTag("Client", "#ff4d4d")
STD = b"http://ns.adobe.com/xap/1.0/\x00"


def make_jpeg() -> bytes:
    im = Image.new("RGB", (32, 24), (200, 30, 40))
    for x in range(24):
        im.putpixel((x, x), (x, 255 - x, 7))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def make_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (5, 90, 200)).save(buf, "PNG")
    return buf.getvalue()


def packet(rating_xml: str = "", attr: str = "") -> bytes:
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
        ' xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"'
        f' photoshop:City="Cluj"{attr}>{rating_xml}</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    ).encode("utf-8")


def seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def jpeg_with(pk: bytes) -> bytes:
    data = make_jpeg()
    at = 2
    while data[at + 1] == 0xE0:
        at += 2 + struct.unpack(">H", data[at + 2 : at + 4])[0]
    return data[:at] + seg(0xE1, STD + pk) + data[at:]


def rating_of_bytes(data: bytes) -> str | None:
    """The raw xmp:Rating text of an embedded jpeg (test-side regex, independent of the module's parser)."""
    m = re.search(rb"<xmp:Rating>([^<]*)</xmp:Rating>|xmp:Rating=\"([^\"]*)\"", data)
    return None if m is None else (m.group(1) or m.group(2)).decode()


def pic(primary: Path, sidecar=None, companions=()):
    return SimpleNamespace(stem=primary.stem, primary=primary, sidecar=sidecar, companions=tuple(companions))


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ------------------------------------------------------------------ packet / embed -------------------------------------


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
def test_embed_jpeg_png_rating_and_sidecar(tmp_path: Path, n: int):
    jpg = write(tmp_path, "a.jpg", make_jpeg())
    assert xmp.apply_embedded(jpg, [], rating=n).status == "written"
    assert rating_of_bytes(jpg.read_bytes()) == str(n)
    png = write(tmp_path, "a.png", make_png())
    assert xmp.apply_embedded(png, [CLIENT], rating=n).status == "written"
    assert xmp.read_embedded(png) is not None
    assert xmp.parse_pills(xmp.read_embedded(png)) == [CLIENT]
    assert rating_of_bytes(xmp.read_embedded(png)) == str(n)
    side = tmp_path / "a.xmp"
    assert xmp.write_sidecar(side, [], rating=n).status == "written"
    assert rating_of_bytes(side.read_bytes()) == str(n)


def test_rating_zero_removes_one_to_five_and_keeps_reject():
    five = xmp.build_packet([], rating=5)
    assert rating_of_bytes(xmp.build_packet([], five, rating=0)) is None
    reject = packet("<xmp:Rating>-1</xmp:Rating>")
    assert rating_of_bytes(xmp.build_packet([CLIENT], reject, rating=0)) == "-1"
    assert rating_of_bytes(xmp.build_packet([CLIENT], reject, rating=3)) == "3"


def test_rating_none_leaves_foreign_rating():
    foreign = packet("<xmp:Rating>4</xmp:Rating>")
    assert rating_of_bytes(xmp.build_packet([CLIENT], foreign)) == "4"
    assert rating_of_bytes(xmp.build_packet([CLIENT], foreign, rating=None)) == "4"


def test_rating_attribute_form_is_replaced_not_duplicated():
    foreign = packet(attr=' xmp:Rating="2"')
    out = xmp.build_packet([], foreign, rating=5)
    assert out.count(b"Rating") == 2 or out.count(b"xmp:Rating") == 1  # one attribute, or one element
    assert xmp.parse_rating(out) == 5


def test_embed_rating_only_change_writes_and_same_rating_is_noop(tmp_path: Path):
    jpg = write(tmp_path, "b.jpg", make_jpeg())
    assert xmp.apply_embedded(jpg, [CLIENT], rating=2).status == "written"
    before = jpg.read_bytes()
    again = xmp.apply_embedded(jpg, [CLIENT], rating=2)
    assert again.reason == "nothing to change" and jpg.read_bytes() == before
    assert xmp.apply_embedded(jpg, [CLIENT], rating=4).status == "written"
    assert jpg.read_bytes() != before and rating_of_bytes(jpg.read_bytes()) == "4"
    # None never counts as a change
    mid = jpg.read_bytes()
    assert xmp.apply_embedded(jpg, [CLIENT]).reason == "nothing to change" and jpg.read_bytes() == mid
    # PNG: the same
    png = write(tmp_path, "b.png", make_png())
    xmp.apply_embedded(png, [], rating=1)
    p1 = png.read_bytes()
    assert xmp.apply_embedded(png, [], rating=1).reason == "nothing to change"
    assert xmp.apply_embedded(png, [], rating=3).status == "written" and png.read_bytes() != p1


def test_sidecar_rating_only_change_writes(tmp_path: Path):
    side = tmp_path / "c.xmp"
    assert xmp.write_sidecar(side, [CLIENT], rating=2).status == "written"
    assert xmp.write_sidecar(side, [CLIENT], rating=2).reason == "nothing to change"
    assert xmp.write_sidecar(side, [CLIENT], rating=5).status == "written"
    assert xmp.parse_rating(side.read_bytes()) == 5
    assert xmp.write_sidecar(side, [CLIENT], rating=0).status == "written"
    assert xmp.parse_rating(side.read_bytes()) is None
    assert xmp.parse_pills(side.read_bytes()) == [CLIENT]


def test_rating_zero_on_missing_sidecar_or_plain_jpeg_writes_nothing(tmp_path: Path):
    side = tmp_path / "d.xmp"
    assert xmp.write_sidecar(side, [], rating=0).reason == "nothing to change" and not side.exists()
    jpg = write(tmp_path, "d.jpg", make_jpeg())
    before = jpg.read_bytes()
    assert xmp.apply_embedded(jpg, [], rating=0).reason == "nothing to change" and jpg.read_bytes() == before


def test_verify_does_not_decline_a_foreign_rating_change(tmp_path: Path):
    side = write(tmp_path, "e.xmp", packet("<xmp:Rating>4</xmp:Rating>"))
    out = xmp.write_sidecar(side, [CLIENT], rating=2)
    assert out.status == "written", out
    assert xmp.parse_rating(side.read_bytes()) == 2
    attr = write(tmp_path, "f.xmp", packet(attr=' xmp:Rating="4"'))
    assert xmp.write_sidecar(attr, [], rating=3).status == "written"
    assert xmp.parse_rating(attr.read_bytes()) == 3
    # but other foreign content is still protected when no rating is passed
    keep = write(tmp_path, "g.xmp", packet("<xmp:Rating>4</xmp:Rating>"))
    assert xmp.write_sidecar(keep, [CLIENT]).status == "written"
    assert xmp.parse_rating(keep.read_bytes()) == 4
    assert b"Cluj" in keep.read_bytes()


# ------------------------------------------------------------------ read -----------------------------------------------


@pytest.mark.parametrize("text,want", [
    ("3", 3), ("3.0", 3), (" 5 ", 5), ("1", 1), ("-1", None), ("0", None), ("6", None), ("99", None),
    ("junk", None), ("", None), ("nan", None), ("inf", None), ("-inf", None), ("1e400", None),
])
def test_parse_rating_tolerant_element_and_attribute(text: str, want):
    assert xmp.parse_rating(packet(f"<xmp:Rating>{text}</xmp:Rating>")) == want
    assert xmp.parse_rating(packet(attr=f' xmp:Rating="{text}"')) == want


def test_parse_rating_hostile_packets():
    assert xmp.parse_rating(b"") is None
    assert xmp.parse_rating(b"not xml") is None
    assert xmp.parse_rating(b"<a>" * 5000) is None
    deep = b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>" + b"<a>" * 5000 + b"</a>" * 5000 + b"</x:xmpmeta>"
    assert xmp.parse_rating(deep) is None
    assert xmp.parse_rating(None) is None


def test_read_picture_meta_foreign_rating_without_bag(tmp_path: Path):
    jpg = write(tmp_path, "h.jpg", jpeg_with(packet("<xmp:Rating>4</xmp:Rating>")))
    assert xmp.read_picture_meta(pic(jpg)) == (None, 4)
    assert xmp.read_picture_pills(pic(jpg)) is None
    plain = write(tmp_path, "i.jpg", make_jpeg())
    assert xmp.read_picture_meta(pic(plain)) == (None, None)
    assert xmp.read_picture_meta(pic(tmp_path / "gone.jpg")) == (None, None)


def test_read_picture_meta_reject_is_no_stars_and_stops_the_search(tmp_path: Path):
    jpg = write(tmp_path, "j.jpg", jpeg_with(packet("<xmp:Rating>-1</xmp:Rating>")))
    side = write(tmp_path, "j.xmp", packet("<xmp:Rating>5</xmp:Rating>"))
    assert xmp.read_picture_meta(pic(jpg, None, [side])) == (None, None)  # first store with a Rating at all


def test_read_picture_meta_precedence_and_pills_unchanged(tmp_path: Path):
    raf = write(tmp_path, "k.raf", b"x" * 100)
    jpg = write(tmp_path, "k.jpg", make_jpeg())
    side = tmp_path / "k.xmp"
    xmp.apply_embedded(jpg, [CLIENT], rating=2)
    xmp.write_sidecar(side, [], rating=5)
    # RAW primary: the sidecar first. It has a Rating (5) but no bag: pills come from the twin, rating from the sidecar.
    assert xmp.read_picture_meta(pic(raf, jpg, [side])) == ([CLIENT], 5)
    assert xmp.read_picture_pills(pic(raf, jpg, [side])) == [CLIENT]
    # JPEG primary: embedded first
    assert xmp.read_picture_meta(pic(jpg, None, [side])) == ([CLIENT], 2)
    assert "read_picture_meta" in xmp.__all__


# ------------------------------------------------------------------ the per-app index ----------------------------------


def make_index(tmp_path: Path):
    f = tmp_path / "img.jpg"
    f.write_bytes(make_jpeg())
    return pills_mod.PillIndex(tmp_path / "index.json"), pic(f)


def test_index_stars_roundtrip(tmp_path: Path):
    idx, p = make_index(tmp_path)
    assert idx.stars(p) is None
    idx.set_stars(p, 4)
    assert idx.stars(p) == 4 and idx.get(p) == ()
    assert idx.save() is True
    doc = json.loads((tmp_path / "index.json").read_text("utf-8"))
    (entry,) = doc["entries"].values()
    assert entry["stars"] == 4 and entry["names"] == []
    again = pills_mod.PillIndex.load(tmp_path / "index.json")
    assert again.stars(p) == 4
    idx.set_stars(p, 0)
    assert idx.stars(p) == 0
    idx.set_stars(p, None)
    assert idx.stars(p) is None and idx._entries == {}


def test_index_stars_with_pills_survive_clear_rename_drop(tmp_path: Path):
    idx, p = make_index(tmp_path)
    idx.set(p, ["Hero", "Print"])
    idx.set_stars(p, 3)
    idx.clear(p)
    assert idx.get(p) == () and idx.stars(p) == 3
    idx.set(p, ["Hero", "Print"])
    idx.rename_pill("Hero", "Star")
    assert idx.get(p) == ("Star", "Print") and idx.stars(p) == 3
    idx.drop_pill("Print")
    idx.drop_pill("Star")
    assert idx.get(p) == () and idx.stars(p) == 3
    idx.set(p, [])
    assert idx.stars(p) == 3  # emptying the names keeps the entry with stars
    idx.toggle(p, "A")
    idx.toggle(p, "A")
    assert idx.stars(p) == 3


def test_index_set_stars_keeps_names_and_validates(tmp_path: Path):
    idx, p = make_index(tmp_path)
    idx.set(p, ["Hero"])
    idx.set_stars(p, 2)
    assert idx.get(p) == ("Hero",)
    for bad in (6, -1, True, 2.5, "3"):
        with pytest.raises(ValueError):
            idx.set_stars(p, bad)
    assert idx.stars(p) == 2


def test_index_load_old_file_and_invalid_stars(tmp_path: Path):
    path = tmp_path / "index.json"
    now = pills_mod._now()
    rows = {
        "old": {"names": ["A"], "at": now},
        "good": {"names": ["B"], "stars": 5, "at": now},
        "boolstar": {"names": ["C"], "stars": True, "at": now},
        "strstar": {"names": ["D"], "stars": "3", "at": now},
        "bigstar": {"names": ["E"], "stars": 9, "at": now},
        "floatstar": {"names": ["F"], "stars": 3.0, "at": now},
        "onlystars": {"names": [], "stars": 2, "at": now},
        "nothing": {"names": [], "at": now},
        "badonly": {"names": [], "stars": 77, "at": now},
        "zero": {"names": [], "stars": 0, "at": now},
    }
    path.write_text(json.dumps({"version": pills_mod.VERSION, "entries": rows}), "utf-8")
    e = pills_mod.PillIndex.load(path)._entries
    assert e["old"] == {"names": ["A"], "at": now}
    assert e["good"]["stars"] == 5
    for k, n in (("boolstar", "C"), ("strstar", "D"), ("bigstar", "E"), ("floatstar", "F")):
        assert e[k]["names"] == [n] and "stars" not in e[k]
    assert e["onlystars"]["stars"] == 2 and e["onlystars"]["names"] == []
    assert e["zero"]["stars"] == 0
    assert "nothing" not in e and "badonly" not in e
