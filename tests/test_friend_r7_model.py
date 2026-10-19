"""Derived-picture model (round 7): pure, in memory, no Qt."""

import time

import pytest

from spuza import derived, ratio
from spuza.derived import CROP, STORY, Band, Derived
from spuza.story import Story

R45 = ratio.Ratio(4)
RECT = (0.1, 0.2, 0.6, 0.9)
NAN, INF = float("nan"), float("inf")


def crop_item(b, src, **kw):
    return b.add(src, CROP, crop=kw.pop("crop", RECT), **kw)


def test_add_ids_and_order():
    b = Band()
    a = crop_item(b, 3)
    c = b.add(1, STORY, story=Story(1.5, 0.1, -0.1))
    d = crop_item(b, 3, ratio=R45)
    assert (a.id, c.id, d.id) == (1, 2, 3)
    assert [x.id for x in b.items] == [2, 1, 3]
    assert [x.id for x in b.of(3)] == [1, 3]
    assert b.of(7) == ()
    assert b.get(2) is c and b.get(99) is None
    assert len(b) == 3 and bool(b) and list(b) == list(b.items)
    assert not Band() and len(Band()) == 0
    assert a.marked is True and a.ratio is None


def test_add_normalises_crop():
    d = Band().add(0, CROP, crop=[0.9, 0.9, 0.1, 0.1])
    assert d.crop == (0.1, 0.1, 0.9, 0.9)


@pytest.mark.parametrize("args", [
    (0, "zoom", {}),
    (0, CROP, {}),
    (0, CROP, {"crop": (0.5, 0.5, 0.5, 0.5)}),
    (0, CROP, {"crop": "nope"}),
    (0, CROP, {"crop": (0, 0, NAN, 1)}),
    (0, STORY, {}),
    (0, STORY, {"story": (1, 0, 0)}),
    (-1, CROP, {"crop": RECT}),
    (True, CROP, {"crop": RECT}),
    ("1", CROP, {"crop": RECT}),
])
def test_add_rejects(args):
    src, kind, kw = args
    b = Band()
    with pytest.raises(ValueError):
        b.add(src, kind, **kw)
    assert len(b) == 0 and b.version == 0


def test_ids_never_reused():
    b = Band()
    crop_item(b, 0)
    crop_item(b, 0)
    assert b.remove(2).id == 2
    assert crop_item(b, 0).id == 3
    b.remove(3)
    b.remove(1)
    assert crop_item(b, 5).id == 4
    assert b.remove(1) is None


def test_replace_keeps_id_and_position():
    b = Band()
    a = crop_item(b, 0)
    crop_item(b, 0)
    new = b.replace(a.id, crop=(0, 0, 0.5, 0.5), ratio=R45)
    assert new.id == a.id and new.crop == (0, 0, 0.5, 0.5) and new.ratio == R45
    assert [x.id for x in b.of(0)] == [1, 2]
    assert b.get(1) is new
    assert b.replace(99, marked=False) is None


def test_replace_cannot_change_identity_or_go_invalid():
    b = Band()
    a = crop_item(b, 0)
    for changes in ({"id": 9}, {"source": 4}, {"kind": STORY}, {"crop": "bad"}, {"bogus": 1}):
        assert b.replace(a.id, **changes) is None
    assert b.get(1) == a and b.get(9) is None


def test_set_marked_and_version():
    b = Band()
    a = crop_item(b, 0)
    v = b.version
    assert b.set_marked(a.id, False) is True
    assert b.get(a.id).marked is False and b.version > v
    assert b.set_marked(99, True) is False


def test_remove():
    b = Band()
    a = crop_item(b, 0)
    v = b.version
    assert b.remove(a.id) == a
    assert b.version > v and len(b) == 0
    v = b.version
    assert b.remove(a.id) is None
    assert b.version == v


def test_version_bumps_on_each_mutation():
    b = Band()
    seen = [b.version]
    d = crop_item(b, 0)
    seen.append(b.version)
    b.replace(d.id, ratio=R45)
    seen.append(b.version)
    b.set_marked(d.id, False)
    seen.append(b.version)
    b.remove(d.id)
    seen.append(b.version)
    b.reset([])
    seen.append(b.version)
    assert seen == sorted(set(seen))


def test_rows():
    b = Band()
    crop_item(b, 1)
    crop_item(b, 1)
    crop_item(b, 9)  # beyond n
    assert b.rows(3) == ((0, None), (1, None), (1, 1), (1, 2), (2, None))
    assert b.rows(0) == ()
    assert b.rows(-4) == ()
    assert b.rows(10)[-2:] == ((9, None), (9, 3))


def test_rows_cached_and_invalidated():
    b = Band()
    crop_item(b, 1)
    r1 = b.rows(5)
    assert b.rows(5) is r1
    crop_item(b, 2)
    r2 = b.rows(5)
    assert r2 is not r1 and (2, 2) in r2
    assert b.rows(5) is r2
    b.remove(2)
    assert (2, 2) not in b.rows(5)
    assert b.rows(4) == ((0, None), (1, None), (1, 1), (2, None), (3, None))


def test_rows_fast_for_a_big_filmstrip():
    b = Band()
    for i in range(500):
        crop_item(b, i * 4)
    t = time.perf_counter()
    rows = b.rows(2000)
    build = time.perf_counter() - t
    assert len(rows) == 2500
    assert build < 0.05
    t = time.perf_counter()
    for _ in range(10000):
        assert b.rows(2000) is rows
    assert time.perf_counter() - t < 0.5


def test_row_of():
    b = Band()
    crop_item(b, 1)
    d2 = crop_item(b, 1)
    rows = b.rows(4)
    assert b.row_of(0, None, 4) == 0
    assert b.row_of(1, n=4) == rows.index((1, None))
    assert b.row_of(1, d2.id, 4) == rows.index((1, 2))
    assert b.row_of(3, n=4) == len(rows) - 1
    assert b.row_of(4, n=4) is None
    assert b.row_of(1, 77, n=4) is None
    assert b.row_of(-1, n=4) is None
    crop_item(b, 0)
    assert b.row_of(1, n=4) == b.rows(4).index((1, None))
    b.remove(1)
    assert b.row_of(1, 1, n=4) is None
    with pytest.raises(TypeError):
        b.row_of(1)


def test_snapshot():
    a, b = Band(), Band()
    for x in (a, b):
        crop_item(x, 0)
        x.add(2, STORY, story=Story(2.0, 0.1, 0.0))
    assert a.snapshot() == b.snapshot()
    hash(a.snapshot())
    b.set_marked(1, False)
    assert a.snapshot() != b.snapshot()
    b.set_marked(1, True)
    assert a.snapshot() == b.snapshot()
    b.remove(2)
    assert a.snapshot() != b.snapshot()
    assert Band().snapshot() == Band().snapshot()


def test_reset():
    items = [
        Derived(5, 2, CROP, crop=RECT),
        Derived(3, 0, STORY, story=Story()),
        Derived(5, 9, CROP, crop=RECT),  # duplicate id
        Derived(7, 0, "zoom"),  # invalid kind
        Derived(8, 0, CROP),  # crop kind without crop
        Derived(9, -1, STORY, story=Story()),  # negative source
        Derived(0, 1, STORY, story=Story()),  # id must be positive
        "junk",
        Derived(6, 0, CROP, crop=RECT, marked=False),
    ]
    b = Band()
    crop_item(b, 0)
    b.reset(items)
    assert [x.id for x in b.items] == [3, 6, 5]
    assert b.get(6).marked is False
    assert crop_item(b, 0).id == 7
    b.reset([])
    assert len(b) == 0
    b.reset(None)
    assert len(b) == 0


def test_reset_next_id_continues():
    b = Band()
    b.reset([Derived(4, 0, STORY, story=Story())])
    assert crop_item(b, 0).id == 5


def test_tag_and_export_tail():
    f = Derived(1, 0, CROP, crop=RECT)
    c = Derived(2, 0, CROP, crop=RECT, ratio=R45)
    o = Derived(3, 0, CROP, crop=RECT, ratio=ratio.Ratio(2))
    s = Derived(4, 0, STORY, story=Story())
    assert derived.tag(f) == "crop"
    assert derived.tag(c) == "4:5" and derived.tag(c, 1) == "5:4"
    assert derived.tag(o) == "Original"
    assert derived.tag(s) == "9:16" and derived.tag(s, 3) == "9:16"
    assert derived.export_tail(f) == ".jpg"
    assert derived.export_tail(o) == ".jpg"
    assert derived.export_tail(c) == "_4x5.jpg"
    assert derived.export_tail(c, 1) == "_5x4.jpg"
    assert derived.export_tail(s) == "_story.jpg"
    assert derived.export_tail(s, 1) == "_story.jpg"


def test_round_trip():
    for d in (
        Derived(1, 4, CROP, crop=RECT),
        Derived(2, 4, CROP, crop=RECT, ratio=R45),
        Derived(3, 4, CROP, crop=RECT, ratio=ratio.flip(R45), marked=False),
        Derived(4, 4, STORY, story=Story(2.5, 0.25, -0.5)),
        Derived(5, 4, STORY, story=Story(), marked=False),
    ):
        row = derived.to_row(d)
        assert "source" not in row and "path" not in row
        assert derived.from_row(row, 4) == d
        assert derived.from_row(row, 7).source == 7


def test_to_row_shape():
    assert derived.to_row(Derived(1, 0, CROP, crop=RECT)) == {
        "id": 1, "kind": "crop", "marked": True, "rect": [0.1, 0.2, 0.6, 0.9]}
    assert derived.to_row(Derived(1, 0, CROP, crop=RECT, ratio=R45))["ratio"] == "4"
    assert derived.to_row(Derived(2, 0, STORY, story=Story(2, 0.5, 0))) == {
        "id": 2, "kind": "story", "marked": True, "zoom": 2, "dx": 0.5, "dy": 0}


GOOD = {"id": 3, "kind": "crop", "rect": [0.1, 0.1, 0.5, 0.5]}
STORY_ROW = {"id": 1, "kind": "story", "zoom": 1, "dx": 0, "dy": 0}


@pytest.mark.parametrize("row", [
    None, [], "x", 5, {},
    {**GOOD, "id": 0}, {**GOOD, "id": -2}, {**GOOD, "id": "3"}, {**GOOD, "id": True},
    {**GOOD, "id": 1.5}, {**GOOD, "id": NAN},
    {**GOOD, "kind": "zoom"}, {**GOOD, "kind": None}, {k: v for k, v in GOOD.items() if k != "kind"},
    {**GOOD, "rect": None}, {**GOOD, "rect": [0, 0, 1]}, {**GOOD, "rect": [0, 0, NAN, 1]},
    {**GOOD, "rect": [0, 0, INF, 1]}, {**GOOD, "rect": [True, 0, 1, 1]},
    {**GOOD, "rect": [0.1, 0.1, 0.1, 0.9]}, {**GOOD, "rect": ["a", 0, 1, 1]},
    {**GOOD, "rect": "0123"}, {"id": 1, "kind": "crop"},
    {"id": 1, "kind": "story"}, {**STORY_ROW, "zoom": NAN}, {**STORY_ROW, "dx": INF},
    {**STORY_ROW, "zoom": True}, {**STORY_ROW, "dx": "0"}, {**STORY_ROW, "zoom": 0},
    {k: v for k, v in STORY_ROW.items() if k != "dy"},
])
def test_from_row_rejects(row):
    assert derived.from_row(row, 0) is None


def test_from_row_defaults_and_tolerance():
    d = derived.from_row(GOOD, 2)
    assert d.marked is True and d.ratio is None and d.source == 2
    for bad in ("9", "", None, 4, ["4"], "4tt"):
        assert derived.from_row({**GOOD, "ratio": bad}, 0).ratio is None
    assert derived.from_row({**GOOD, "ratio": "4t"}, 0).ratio == ratio.decode("4t")
    assert derived.from_row({**GOOD, "marked": False}, 0).marked is False
    assert derived.from_row({**GOOD, "marked": "no"}, 0).marked is True
    assert derived.from_row({**GOOD, "rect": [0.5, 0.5, 0.1, 0.1]}, 0).crop == (0.1, 0.1, 0.5, 0.5)
    assert derived.from_row({**GOOD, "rect": (0.1, 0.1, 0.5, 0.5)}, 0).crop == (0.1, 0.1, 0.5, 0.5)
    assert derived.from_row(GOOD, -1) is None
    assert derived.from_row(GOOD, "x") is None


def test_from_row_deep_nesting_is_clean():
    deep = []
    for _ in range(5000):
        deep = [deep]
    assert derived.from_row({**GOOD, "rect": deep}, 0) is None
    assert derived.from_row({**GOOD, "ratio": deep}, 0).ratio is None
    assert derived.from_row({**GOOD, "marked": deep}, 0).marked is True
    assert derived.from_row({"id": deep, "kind": deep}, 0) is None
    assert derived.from_row({**STORY_ROW, "zoom": deep}, 0) is None
