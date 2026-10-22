"""bug-455: un-starring a rejected (-1) picture and re-copying must re-embed in place, not rename the group."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from PIL import Image

from spuza import finder_tags, xmp
from spuza.copy import plan_copy, run_copy

from test_ui_copy_sheet import _APP  # noqa: E402,F401  (makes the QApplication before any widget)

REJECT_XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
              b'<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="-1"/>'
              b'</rdf:RDF></x:xmpmeta>')


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(finder_tags, "write_tags", lambda path, pills: False)


def _card(card: Path) -> list[Path]:
    card.mkdir(parents=True)
    raw = card / "A.NEF"
    raw.write_bytes(b"NIKON-FAKE-RAW " + random.Random(3).randbytes(2000))
    jpg = card / "A.JPG"
    im = Image.new("RGB", (24, 16))
    im.putdata([(x * 10 % 256, y * 15 % 256, (x + y) % 256) for y in range(16) for x in range(24)])
    im.save(jpg, "JPEG", quality=90)
    comp = card / "A.xmp"
    comp.write_bytes(REJECT_XMP)
    return [raw, jpg, comp]


def _snap(files):
    return {f: f.read_bytes() for f in files}


def _rating_xmp(p: Path):
    return xmp.parse_rating(p.read_bytes())


def test_unstar_rejected_picture_recopies_in_place(tmp_path):
    files = _card(tmp_path / "card")
    raw, jpg, comp = files
    before = _snap(files)
    out = tmp_path / "out"
    stars = {raw: 4, jpg: 4}

    res = run_copy(plan_copy([files], out, ratings=stars))
    assert not res.failed
    assert _rating_xmp(out / "A.xmp") == 4  # the -1 is replaced by the user's stars
    assert xmp.parse_rating(xmp.read_embedded(out / "A.JPG")) == 4
    assert _snap(files) == before

    plan = plan_copy([files], out, ratings=stars)  # same stars again: skip
    assert {i.action for i in plan.items} == {"skip"}
    run_copy(plan)
    assert _rating_xmp(out / "A.xmp") == 4 and _snap(files) == before

    zero = {raw: 0, jpg: 0}  # the user clears the stars
    plan = plan_copy([files], out, ratings=zero)
    assert {i.action for i in plan.items} == {"skip"}, [(i.src.name, i.action, i.dst) for i in plan.items]
    res = run_copy(plan)
    assert not res.failed
    assert sorted(p.name for p in out.iterdir()) == ["A.JPG", "A.NEF", "A.xmp"]  # no A-1.* duplicates
    assert _rating_xmp(out / "A.xmp") is None  # 4 is gone; the replaced -1 is not restored
    assert xmp.parse_rating(xmp.read_embedded(out / "A.JPG")) is None
    assert _snap(files) == before


def test_plain_copy_against_differing_destination_still_renames(tmp_path):
    files = _card(tmp_path / "card")
    out = tmp_path / "out"
    run_copy(plan_copy([files], out, ratings={files[0]: 4, files[1]: 4}))
    plan = plan_copy([files], out)  # no pills, no ratings: today's behaviour
    assert any(i.action != "skip" for i in plan.items)


def test_genuinely_different_companion_still_renames(tmp_path):
    files = _card(tmp_path / "card")
    out = tmp_path / "out"
    run_copy(plan_copy([files], out, ratings={files[0]: 4, files[1]: 4}))
    (out / "A.xmp").write_bytes(REJECT_XMP.replace(b"</rdf:RDF>", b'<!-- other --></rdf:RDF>').replace(
        b'xmp:Rating="-1"', b'xmp:Rating="4" xmp:Label="Red"'))
    plan = plan_copy([files], out, ratings={files[0]: 0, files[1]: 0})
    assert any(i.action != "skip" for i in plan.items)
