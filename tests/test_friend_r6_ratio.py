"""Round 6: the pure ratio-crop model (no widgets, no images; synthetic sizes only)."""

import math

import pytest

from spuza import ratio
from spuza.crop import MIN_SIDE, normalise
from spuza.ratio import FREE, Ratio

W, H = 6000, 4000

# design.md section 3, "largest crop on 6000x4000": (entry, turned) -> pixels
TABLE = {
    (3, False): (4000, 4000),
    (4, False): (3200, 4000),
    (4, True): (5000, 4000),
    (5, False): (3000, 4000),
    (5, True): (5333, 4000),
    (6, False): (2667, 4000),
    (6, True): (6000, 4000),
    (7, False): (2250, 4000),
    (7, True): (6000, 3375),
    (8, False): (6000, 3144),
    (8, True): (2096, 4000),
}


def test_entries_and_names():
    assert [n for _, n in ratio.ENTRIES] == ["Free", "Original", "1:1", "4:5", "3:4", "2:3", "9:16", "1.91:1"]
    assert [k for k, _ in ratio.ENTRIES] == list(range(1, 9))
    assert [ratio.name(k) for k in range(1, 9)] == [n for _, n in ratio.ENTRIES]
    assert FREE == Ratio(1)


@pytest.mark.parametrize("key", sorted(TABLE))
def test_design_table_pixels(key):
    entry, turned = key
    a = ratio.frame_aspect(Ratio(entry, turned), W, H)
    box = ratio.largest_centred(a, W, H)
    assert normalise(box) == pytest.approx(box)
    assert ratio.pixel_size(box, W, H) == TABLE[key]


def test_original_and_free_aspect():
    assert ratio.frame_aspect(Ratio(2), W, H) == pytest.approx(1.5)
    assert ratio.frame_aspect(Ratio(2, True), W, H) == pytest.approx(2 / 3)
    assert ratio.frame_aspect(FREE, W, H) is None
    assert ratio.frame_aspect(Ratio(8), W, H) == pytest.approx(1080 / 566)
    assert ratio.pixel_size(ratio.largest_centred(None, W, H), W, H) == (W, H)
    assert ratio.largest_centred(None, W, H) == (0.0, 0.0, 1.0, 1.0)
    assert ratio.pixel_size(ratio.largest_centred(1.5, W, H), W, H) == (W, H)


def test_opening_unturned():
    assert ratio.opening(1, W, H) == Ratio(1)
    assert ratio.opening(2, W, H) == Ratio(2, False)
    assert ratio.opening(3, W, H) == Ratio(3, False)
    for e in (4, 5, 6, 7):
        assert ratio.opening(e, W, H) == Ratio(e, False)
    assert ratio.opening(8, W, H) == Ratio(8, False)


def test_opening_turned_once_and_portrait_picture():
    for e in (4, 5, 6, 7):
        assert ratio.opening(e, W, H, turns=1) == Ratio(e, True)
        assert ratio.opening(e, W, H, turns=2) == Ratio(e, False)
        assert ratio.opening(e, W, H, turns=3) == Ratio(e, True)
    assert ratio.opening(8, W, H, turns=1) == Ratio(8, True)
    for e in (1, 2, 3):
        assert not ratio.opening(e, W, H, turns=1).turned
    # a 4000x6000 stored frame is portrait already: on screen it is still the 4:5 portrait
    r = ratio.opening(4, 4000, 6000)
    assert r == Ratio(4, False)
    box = ratio.largest_centred(ratio.frame_aspect(r, 4000, 6000), 4000, 6000)
    assert ratio.pixel_size(box, 4000, 6000) == (4000, 5000)
    # and a square picture
    box = ratio.largest_centred(ratio.frame_aspect(ratio.opening(5, 3000, 3000), 3000, 3000), 3000, 3000)
    assert ratio.pixel_size(box, 3000, 3000) == (2250, 3000)
    assert ratio.pixel_size(ratio.largest_centred(1.0, 3000, 3000), 3000, 3000) == (3000, 3000)


def test_turned_on_screen_crop_is_portrait():
    # one clockwise turn: stored frame 6000x4000 shows as 4000x6000; 4:5 portrait on screen is 5:4 stored
    r = ratio.opening(4, W, H, turns=1)
    box = ratio.largest_centred(ratio.frame_aspect(r, W, H), W, H)
    assert ratio.pixel_size(box, W, H) == (5000, 4000)
    assert ratio.label(r, turns=1) == "4:5"


def test_flip():
    assert ratio.flip(Ratio(4)) == Ratio(4, True)
    assert ratio.flip(Ratio(4, True)) == Ratio(4)
    assert ratio.flip(ratio.flip(Ratio(8))) == Ratio(8)
    assert ratio.flip(FREE) is FREE
    assert ratio.flip(Ratio(3)) == Ratio(3)
    assert ratio.flip(Ratio(2)) == Ratio(2, True)


def test_labels():
    assert ratio.label(FREE) == "Free"
    assert ratio.label(Ratio(2)) == "Original"
    assert ratio.label(Ratio(3)) == "1:1"
    pairs = {4: ("4:5", "5:4"), 5: ("3:4", "4:3"), 6: ("2:3", "3:2"), 7: ("9:16", "16:9"), 8: ("1.91:1", "1:1.91")}
    for e, (a, b) in pairs.items():
        assert ratio.label(Ratio(e)) == a
        assert ratio.label(Ratio(e, True)) == b
        assert ratio.label(Ratio(e), turns=1) == b
        assert ratio.label(Ratio(e, True), turns=1) == a
        assert ratio.label(Ratio(e), turns=2) == a
        assert ratio.label(Ratio(e), turns=3) == b
    assert ratio.label(Ratio(3), turns=1) == "1:1"
    assert ratio.label(Ratio(2), turns=1) == "Original"


def test_export_suffix():
    assert ratio.export_suffix(FREE) == ""
    assert ratio.export_suffix(Ratio(2)) == ""
    assert ratio.export_suffix(Ratio(2, True), turns=1) == ""
    assert ratio.export_suffix(Ratio(3)) == "_1x1"
    want = {
        (4, False): "_4x5", (4, True): "_5x4", (5, False): "_3x4", (5, True): "_4x3",
        (6, False): "_2x3", (6, True): "_3x2", (7, False): "_9x16", (7, True): "_16x9",
        (8, False): "_191x100", (8, True): "_100x191",
    }  # fmt: skip
    for (e, t), s in want.items():
        assert ratio.export_suffix(Ratio(e, t)) == s
    assert ratio.export_suffix(Ratio(4), turns=1) == "_5x4"
    assert ratio.export_suffix(Ratio(8, True), turns=3) == "_191x100"


def test_encode_decode_round_trip():
    for e in range(1, 9):
        for t in (False, True):
            r = Ratio(e, t)
            assert ratio.decode(ratio.encode(r)) == (r if e not in (1, 3) else Ratio(e))
    assert isinstance(ratio.encode(Ratio(4, True)), str)


@pytest.mark.parametrize(
    "junk",
    [None, True, False, 4, 4.0, float("nan"), "", " ", "0", "9", "10", "-1", "4T", "4tt", "4 t", "x", "٤", "４", b"4",
     [4], (4, True), {"entry": 4}, {4: 5}, object(), [[[[]]]], "4\n", "\x00", "4t\x00", Ratio(4), 10**400],
)
def test_decode_garbage_is_none(junk):
    assert ratio.decode(junk) is None


def test_decode_deeply_nested_never_raises():
    deep = []
    for _ in range(5000):
        deep = [deep]
    assert ratio.decode(deep) is None
    assert ratio.decode("4" * 100000) is None


def test_decode_normalises_unturnable():
    assert ratio.decode("1t") == FREE
    assert ratio.decode("3t") == Ratio(3)


def test_reshape_none_aspect_returns_normalised_crop():
    assert ratio.reshape((0.8, 0.9, 0.2, 0.1), None, W, H) == (0.2, 0.1, 0.8, 0.9)
    assert ratio.reshape((0.2, 0.1, 0.8, 0.9), None, W, H) == (0.2, 0.1, 0.8, 0.9)


@pytest.mark.parametrize("junk", [None, "x", (1, 2), (0.5, 0.5, 0.5, 0.5), (float("nan"), 0, 1, 1), (True, 0, 1, 1), [[]]])
def test_reshape_garbage_crop_is_largest_centred(junk):
    got = ratio.reshape(junk, 1.0, W, H)
    assert got == ratio.largest_centred(1.0, W, H)
    assert ratio.pixel_size(got, W, H) == (4000, 4000)


def test_reshape_keeps_centre_and_area():
    crop = (0.1, 0.2, 0.5, 0.6)  # 2400x1600 px, centre (0.3, 0.4)
    out = ratio.reshape(crop, 1.0, W, H)
    assert normalise(out) is not None
    cx, cy = (out[0] + out[2]) / 2, (out[1] + out[3]) / 2
    assert (cx, cy) == pytest.approx((0.3, 0.4))
    pw, ph = (out[2] - out[0]) * W, (out[3] - out[1]) * H
    assert pw == pytest.approx(ph)
    assert pw * ph == pytest.approx(2400 * 1600)


def test_reshape_shrinks_and_slides_inside():
    crop = (0.0, 0.0, 0.5, 0.5)  # 3000x2000 at the corner
    out = ratio.reshape(crop, 9 / 16, W, H)
    assert normalise(out) == pytest.approx(out)
    assert all(0.0 <= v <= 1.0 for v in out)
    pw, ph = (out[2] - out[0]) * W, (out[3] - out[1]) * H
    assert pw / ph == pytest.approx(9 / 16)
    assert pw * ph == pytest.approx(3000 * 2000)
    # forced shrink: a full picture to 1:1 cannot keep its area
    out = ratio.reshape((0.0, 0.0, 1.0, 1.0), 1.0, W, H)
    assert ratio.pixel_size(out, W, H) == (4000, 4000)
    assert out[1] == 0.0 and out[3] == 1.0


def test_reshape_is_only_as_small_as_the_picture_forces():
    out = ratio.reshape((0.05, 0.05, 0.95, 0.95), 4 / 5, W, H)  # 5400x3600 px, 4:5 portrait needs a shrink
    pw, ph = (out[2] - out[0]) * W, (out[3] - out[1]) * H
    assert ph == pytest.approx(H)  # height is the binding side, so it is the full height
    assert pw / ph == pytest.approx(4 / 5)
    assert normalise(out) is not None


def test_press_4_3_4_lands_near_the_start():
    start = ratio.largest_centred(ratio.frame_aspect(Ratio(4), W, H), W, H)
    a3 = ratio.frame_aspect(Ratio(3), W, H)
    a4 = ratio.frame_aspect(Ratio(4), W, H)
    sq = ratio.reshape(start, a3, W, H)
    back = ratio.reshape(sq, a4, W, H)
    assert back == pytest.approx(start, abs=1e-9)
    # off-centre start: the centre survives the round trip
    crop = (0.0, 0.1, 0.4, 0.9)
    there = ratio.reshape(ratio.reshape(crop, a3, W, H), a4, W, H)
    c0 = ((crop[0] + crop[2]) / 2, (crop[1] + crop[3]) / 2)
    c1 = ((there[0] + there[2]) / 2, (there[1] + there[3]) / 2)
    assert math.dist(c0, c1) < 0.2
    assert normalise(there) is not None


def test_reshape_results_always_normalise():
    for aspect in (0.2, 9 / 16, 0.8, 1.0, 1.25, 16 / 9, 1080 / 566, 5.0):
        for crop in [(0, 0, 1, 1), (0.9, 0.9, 1, 1), (0.0, 0.0, 0.02, 0.02), (0.4, 0.4, 0.6, 0.6)]:
            for w, h in [(W, H), (4000, 6000), (3000, 3000)]:
                out = ratio.reshape(crop, aspect, w, h)
                assert normalise(out) == pytest.approx(out)
                assert out[2] - out[0] >= MIN_SIDE and out[3] - out[1] >= MIN_SIDE


@pytest.mark.parametrize("aspect", [0, -1.0, float("nan"), float("inf"), "x", True, 1e-9, 1e9])
def test_bad_aspect_never_raises_and_normalises(aspect):
    out = ratio.largest_centred(aspect, W, H)
    assert normalise(out) is not None
    out = ratio.reshape((0.1, 0.1, 0.5, 0.5), aspect, W, H)
    assert normalise(out) is not None


@pytest.mark.parametrize("size", [(0, 0), (-5, 10), (1, 1), (None, 5)])
def test_bad_sizes_never_raise(size):
    w, h = size
    assert normalise(ratio.largest_centred(1.5, w, h)) is not None
    assert normalise(ratio.reshape((0.1, 0.1, 0.5, 0.5), 1.5, w, h)) is not None
    assert ratio.frame_aspect(Ratio(2), w, h) is None or ratio.frame_aspect(Ratio(2), w, h) > 0


def test_pixel_size_matches_crop_box():
    assert ratio.pixel_size((0.0, 0.0, 0.5, 0.5), 100, 50) == (50, 25)
    assert ratio.pixel_size((0.0, 0.0, 0.001, 0.001), 100, 50) == (1, 1)


def test_other_pictures():
    # 4000x6000 stored frame, 4:5 stays 4:5 (portrait); 5:4 turned is landscape in the frame
    for r, want in [(Ratio(3), (4000, 4000)), (Ratio(4), (4000, 5000)), (Ratio(4, True), (4000, 3200)),
                    (Ratio(7), (3375, 6000)), (Ratio(2), (4000, 6000))]:  # fmt: skip
        box = ratio.largest_centred(ratio.frame_aspect(r, 4000, 6000), 4000, 6000)
        assert ratio.pixel_size(box, 4000, 6000) == want
    for r, want in [(Ratio(3), (3000, 3000)), (Ratio(4), (2400, 3000)), (Ratio(8), (3000, 1572)),
                    (Ratio(2), (3000, 3000))]:  # fmt: skip
        box = ratio.largest_centred(ratio.frame_aspect(r, 3000, 3000), 3000, 3000)
        assert ratio.pixel_size(box, 3000, 3000) == want


def test_module_is_widget_free():
    import sys

    src = open(ratio.__file__, encoding="utf-8").read()
    assert "PySide6" not in src and "PIL" not in src
    assert "spuza.ratio" in sys.modules
