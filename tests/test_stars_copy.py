"""Star ratings through Copy (task sz-stars-core): the stars ride in the copy's XMP beside the pills (dec-058)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from PIL import Image

from spuza import copy as copy_mod
from spuza import finder_tags, xmp
from spuza.catalog import Picture
from spuza.copy import plan_copy, run_copy
from spuza.xmp import PillTag

from test_ui_copy_sheet import _APP  # noqa: E402,F401  (makes the QApplication before any widget)

HERO = PillTag("Hero", "#2fb4e8")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(finder_tags, "write_tags", lambda path, pills: False)


def jpeg(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (24, 16))
    im.putdata([(x * 10 % 256, y * 15 % 256, (x + y) % 256) for y in range(16) for x in range(24)])
    im.save(path, "JPEG", quality=90)
    return path


def raf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"FUJIFILMCCD-RAW " + random.Random(7).randbytes(3000))
    return path


def embedded_rating(path: Path):
    blob = xmp.read_embedded(path)
    return xmp.parse_rating(blob) if blob else None


def test_starred_picture_without_pills_gets_a_packet(tmp_path):
    src = jpeg(tmp_path / "card" / "A.JPG")
    plan = plan_copy([[src]], tmp_path / "out", ratings={src: 4})
    assert plan.items[0].rating == 4 and plan.items[0].pills == ()
    res = run_copy(plan)
    dst = tmp_path / "out" / "A.JPG"
    assert not res.failed and res.pilled == [src]
    assert embedded_rating(dst) == 4
    assert xmp.parse_pills(xmp.read_embedded(dst)) is None
    assert not list((tmp_path / "out").glob(".*spuza-part*"))
    with Image.open(src) as a, Image.open(dst) as b:
        assert a.tobytes() == b.tobytes()


def test_no_ratings_changes_nothing(tmp_path):
    src = jpeg(tmp_path / "card" / "A.JPG")
    plan = plan_copy([[src]], tmp_path / "out")
    assert plan.items[0].rating is None
    run_copy(plan)
    assert (tmp_path / "out" / "A.JPG").read_bytes() == src.read_bytes()


def test_bad_ratings_are_ignored(tmp_path):
    src = jpeg(tmp_path / "card" / "A.JPG")
    for bad in (7, -1, True, 2.5, "3", None):
        assert plan_copy([[src]], tmp_path / "out", ratings={src: bad}).items[0].rating is None
    assert plan_copy([[src]], tmp_path / "out", ratings=object()).items[0].rating is None  # type: ignore[arg-type]
    assert plan_copy([[src]], tmp_path / "out", ratings={src: 0}).items[0].rating == 0


def test_raw_gets_sidecar_with_rating_and_crops_carry_it(tmp_path):
    card = tmp_path / "card"
    r, j = raf(card / "X.RAF"), jpeg(card / "X.JPG")
    plan = plan_copy([[r, j]], tmp_path / "out", ratings={r: 5, j: 5})
    assert [i.rating for i in plan.items] == [5, 5]
    res = run_copy(plan)
    assert not res.failed
    assert xmp.parse_rating((tmp_path / "out" / "X.xmp").read_bytes()) == 5
    assert embedded_rating(tmp_path / "out" / "X.JPG") == 5


def test_recopy_skip_logic_with_rating(tmp_path):
    src = jpeg(tmp_path / "card" / "A.JPG")
    out = tmp_path / "out"
    run_copy(plan_copy([[src]], out, ratings={src: 3}))
    dst = out / "A.JPG"
    first = dst.read_bytes()
    # the same rating again: a skip, nothing rewritten
    plan = plan_copy([[src]], out, ratings={src: 3})
    assert plan.items[0].action == "skip"
    res = run_copy(plan)
    assert res.skipped == [src] and dst.read_bytes() == first and not list(out.glob("A-*"))
    # a different rating: still our copy (skip, not a rename), and the skip re-embeds the new stars
    plan = plan_copy([[src]], out, ratings={src: 5})
    assert plan.items[0].action == "skip"
    res = run_copy(plan)
    assert res.skipped == [src] and res.pilled == [src]
    assert embedded_rating(dst) == 5 and not list(out.glob("A-*"))
    # 0 clears it in our copy
    plan = plan_copy([[src]], out, ratings={src: 0})
    assert plan.items[0].action == "skip"
    run_copy(plan)
    assert embedded_rating(dst) is None


def test_same_planned_rating_cases(tmp_path):
    src = jpeg(tmp_path / "card" / "A.JPG")
    carrying, other, plain = tmp_path / "c.jpg", tmp_path / "o.jpg", tmp_path / "p.jpg"
    for p, n in ((carrying, 2), (other, 4)):
        p.write_bytes(src.read_bytes())
        assert xmp.apply_embedded(p, [], n).status == "written"
    plain.write_bytes(src.read_bytes())
    assert copy_mod._same_planned(src, carrying, 0, (), rating=2)  # already ours: the same
    assert copy_mod._same_planned(src, other, 0, (), rating=2)  # differs only in the rating: same file, to re-embed
    assert copy_mod._same_planned(src, plain, 0, (), rating=None)
    assert not copy_mod._same_planned(src, carrying, 0, (), rating=None)  # a rated copy is not a plain copy of src
    # a destination with a different picture is never the same
    Image.new("RGB", (30, 20), (9, 9, 9)).save(other, "JPEG")
    assert not copy_mod._same_planned(src, other, 0, (), rating=2)


def test_skip_with_foreign_rating_in_dst_and_pills_is_not_clobbered_without_stars(tmp_path):
    src = jpeg(tmp_path / "card" / "A.JPG")
    out = tmp_path / "out"
    run_copy(plan_copy([[src]], out, pills={src: (HERO,)}))
    dst = out / "A.JPG"
    before = dst.read_bytes()
    plan = plan_copy([[src]], out, pills={src: (HERO,)})  # no ratings: nothing about stars changes
    assert plan.items[0].action == "skip"
    run_copy(plan)
    assert dst.read_bytes() == before


def test_copy_sheet_passes_ratings_to_plan(tmp_path):
    from spuza.ui.copy_sheet import CopySheet

    card = tmp_path / "card"
    card.mkdir()
    primary = card / "S.RAF"
    primary.write_bytes(b"x" * 100)
    twin = card / "S.JPG"
    twin.write_bytes(b"j" * 50)
    comp = card / "S.xmp"
    comp.write_bytes(b"<x/>")
    p = Picture("S", primary, twin, (primary, twin, comp), "RAF+JPG", 150, 0.0, companions=(comp,))
    s = CopySheet([p], tmp_path / "out", ratings={primary: 3, tmp_path / "bad": 99})
    s.free_space_fn = lambda x: 10**12
    s._update_dest()
    assert s._ratings_map() == {primary: 3, twin: 3}  # the picture's files, never the companion
    plan = s._plan()
    ratings = {i.src: i.rating for i in plan.items}
    assert ratings[primary] == 3 and ratings[twin] == 3 and ratings[comp] is None
    # unchanged default
    s2 = CopySheet([p], tmp_path / "out")
    assert s2._ratings_map() == {}
