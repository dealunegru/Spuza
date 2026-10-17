"""Friend round 3: the user's quarter turns become the EXIF Orientation tag in the destination copies only."""

import hashlib
import json
import os
import shutil
import struct
from pathlib import Path

import pytest
from PIL import Image

from spuza import rotation, session
from spuza.catalog import scan
from spuza.copy import plan_copy, run_copy
from spuza.session import SessionStore, card_key
from spuza.ui.copy_sheet import CopySheet
from spuza.ui.resume import ResumeController
from test_ui_copy_sheet import wait_for  # noqa: F401  (also makes the QApplication)
from test_ui_resume import SpyStore, make_card as make_resume_card, open_card

EXIFTOOL = shutil.which("exiftool")
HAS_RAWPY = True
try:
    import rawpy
except Exception:  # noqa: BLE001
    HAS_RAWPY = False


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance()


@pytest.fixture
def advanced():
    from spuza import features

    features.set_mode(features.ADVANCED)


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "logs"))


# ---- helpers -----------------------------------------------------------------------------------------

def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def diff_offsets(a: Path, b: Path) -> list[int]:
    x, y = a.read_bytes(), b.read_bytes()
    assert len(x) == len(y)
    return [i for i in range(len(x)) if x[i] != y[i]]


def make_image(w=24, h=16) -> Image.Image:
    im = Image.new("RGB", (w, h))
    im.putdata([(x * 10 % 256, y * 15 % 256, (x + y) % 256) for y in range(h) for x in range(w)])
    return im


def jpeg(path: Path, orientation: int | None = None, other: bool = False) -> Path:
    """A Pillow JPEG: no EXIF at all, EXIF with only another tag (other), or EXIF carrying the orientation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if orientation is None and not other:
        make_image().save(path, "JPEG")
    else:
        ex = Image.Exif()
        ex[0x010F] = "Spuza test"
        if orientation is not None:
            ex[274] = orientation
        make_image().save(path, "JPEG", exif=ex)
    return path


def copy_one(src: Path, dest: Path, turns: int, **kw):
    plan = plan_copy([[src]], dest, turns={src: turns} if turns else None, **kw)
    res = run_copy(plan)
    assert not res.failed, res.failed
    return res, plan.items[0].dst


# ---- compose ---------------------------------------------------------------------------------------------

def _show(im: Image.Image, orientation: int) -> Image.Image:
    """The picture as displayed under an EXIF orientation (the standard transposes)."""
    T = Image.Transpose
    ops = {1: None, 2: T.FLIP_LEFT_RIGHT, 3: T.ROTATE_180, 4: T.FLIP_TOP_BOTTOM, 5: T.TRANSPOSE,
           6: T.ROTATE_270, 7: T.TRANSVERSE, 8: T.ROTATE_90}
    return im if ops[orientation] is None else im.transpose(ops[orientation])


def test_compose_matches_real_pixels_for_all_32_inputs():
    base = make_image(5, 3)
    for o in range(1, 9):
        for t in range(4):
            shown = _show(base, o)
            for _ in range(t):
                shown = shown.transpose(Image.Transpose.ROTATE_270)  # one clockwise quarter turn
            expect = _show(base, rotation.compose(o, t))
            assert shown.size == expect.size and list(shown.tobytes()) == list(expect.tobytes()), (o, t)


def test_compose_unmirrored_cycle_and_edges():
    seq = [1]
    for _ in range(4):
        seq.append(rotation.compose(seq[-1], 1))
    assert seq == [1, 6, 3, 8, 1]
    assert [rotation.compose(2, t) for t in range(4)] == [2, 7, 4, 5]  # mirrored: 2->7->4->5 clockwise
    for o in range(1, 9):
        assert rotation.compose(o, 0) == o and rotation.compose(o, 4) == o
        assert rotation.compose(rotation.compose(o, 1), 3) == o
    for junk in (0, 9, -1, None, "6", True, 2.5):
        assert rotation.compose(junk, 1) == 6  # unknown counts as unrotated


# ---- JPEG --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("turns", [1, 2, 3])
def test_jpeg_with_exif_is_patched_in_place(tmp_path, turns):
    src = jpeg(tmp_path / "card" / "a.jpg", orientation=1)
    before, mt = src.read_bytes(), src.stat().st_mtime_ns
    res, dst = copy_one(src, tmp_path / "out", turns)
    assert res.rotated == [src] and not res.rotation_kept
    assert rotation.read_orientation(dst) == rotation.compose(1, turns)
    assert len(diff_offsets(src, dst)) in (1, 2)  # only the two bytes of the tag (one may already match)
    assert Image.open(dst).getexif()[274] == rotation.compose(1, turns)
    Image.open(dst).load()
    if EXIFTOOL:
        import subprocess
        out = subprocess.run([EXIFTOOL, "-n", "-s3", "-Orientation", str(dst)], capture_output=True, text=True)
        assert int(out.stdout) == rotation.compose(1, turns)
    assert src.read_bytes() == before and src.stat().st_mtime_ns == mt  # the card file is untouched
    assert dst.stat().st_mtime_ns == mt or abs(dst.stat().st_mtime - src.stat().st_mtime) < 2


def test_jpeg_composes_with_the_camera_orientation(tmp_path):
    src = jpeg(tmp_path / "card" / "a.jpg", orientation=6)
    _, dst = copy_one(src, tmp_path / "out", 1)
    assert rotation.read_orientation(dst) == 3
    _, dst2 = copy_one(src, tmp_path / "out2", 3)
    assert rotation.read_orientation(dst2) == 1


def test_jpeg_without_exif_gets_a_minimal_segment_and_nothing_else_changes(tmp_path):
    src = jpeg(tmp_path / "card" / "n.jpg")
    assert rotation.read_orientation(src) is None
    res, dst = copy_one(src, tmp_path / "out", 1)
    assert res.rotated == [src]
    a, b = src.read_bytes(), dst.read_bytes()
    seg = rotation._minimal_app1(6)
    assert b == a[:2] + seg + a[2:] and len(b) == len(a) + len(seg)  # source == copy minus the inserted segment
    assert rotation.read_orientation(dst) == 6
    assert Image.open(dst).getexif()[274] == 6
    Image.open(dst).load()
    # a second run finds the rotated copy already there and skips it instead of making a "-1" twin
    again = run_copy(plan_copy([[src]], tmp_path / "out", turns={src: 1}))
    assert again.skipped == [src] and not again.copied and sorted(p.name for p in (tmp_path / "out").iterdir()) == ["n.jpg"]


def test_jpeg_with_exif_but_no_orientation_tag_is_kept_and_reported(tmp_path):
    src = jpeg(tmp_path / "card" / "o.jpg", other=True)
    res, dst = copy_one(src, tmp_path / "out", 1)
    assert not res.rotated and len(res.rotation_kept) == 1 and res.rotation_kept[0][0] == src
    assert "no orientation tag" in res.rotation_kept[0][1]
    assert sha(dst) == sha(src)


def test_zero_turns_is_a_plain_copy(tmp_path):
    src = jpeg(tmp_path / "card" / "z.jpg", orientation=8)
    for turns in (None, 0, 4):
        res, dst = copy_one(src, tmp_path / f"out{turns}", turns or 0)
        assert sha(dst) == sha(src) and not res.rotated and not res.rotation_kept
    outcome = rotation.apply_rotation(src, src, 0)
    assert outcome.status == "none"


def _corrupt_tag_type(path: Path) -> None:
    b = bytearray(path.read_bytes())
    for pat, at in ((b"\x12\x01\x03\x00\x01\x00\x00\x00", 2), (b"\x01\x12\x00\x03\x00\x00\x00\x01", 3)):
        i = bytes(b).find(pat)
        if i >= 0:
            b[i + at] = 4  # SHORT -> LONG: the tag is no longer the two bytes we know how to patch
            path.write_bytes(bytes(b))
            return
    raise AssertionError("no orientation entry found")


def test_corrupted_tag_leaves_the_copy_unmodified_and_is_reported(tmp_path):
    src = jpeg(tmp_path / "card" / "c.jpg", orientation=1)
    _corrupt_tag_type(src)
    res, dst = copy_one(src, tmp_path / "out", 1)
    assert not res.rotated and res.rotation_kept[0][0] == src
    assert sha(dst) == sha(src)
    log = res.log_path.read_text(encoding="utf-8")
    assert "KEPT" in log and "original orientation kept" in log


def test_a_failed_check_restores_the_copy(tmp_path, monkeypatch):
    src = jpeg(tmp_path / "card" / "r.jpg", orientation=1)
    monkeypatch.setattr(rotation, "read_all_orientations", lambda *a, **k: [7])
    res, dst = copy_one(src, tmp_path / "out", 1)
    assert not res.rotated and "read back" in res.rotation_kept[0][1]
    assert sha(dst) == sha(src)


def test_an_exiftool_disagreement_restores_the_copy(tmp_path, monkeypatch):
    src = jpeg(tmp_path / "card" / "e.jpg", orientation=1)
    monkeypatch.setattr(rotation, "_which_exiftool", lambda: "exiftool-stub")
    monkeypatch.setattr(rotation, "_exiftool_values", lambda exe, p: [1])
    res, dst = copy_one(src, tmp_path / "out", 1)
    assert not res.rotated and "exiftool reads" in res.rotation_kept[0][1]
    assert sha(dst) == sha(src)


def test_an_exiftool_that_hangs_or_dies_restores_the_copy(tmp_path, monkeypatch):
    import subprocess

    def boom(exe, p):
        raise subprocess.TimeoutExpired("exiftool", 1)

    src = jpeg(tmp_path / "card" / "h.jpg", orientation=1)
    monkeypatch.setattr(rotation, "_which_exiftool", lambda: "exiftool-stub")
    monkeypatch.setattr(rotation, "_exiftool_values", boom)
    res, dst = copy_one(src, tmp_path / "out", 1)
    assert not res.rotated and res.rotation_kept and sha(dst) == sha(src)


def test_without_exiftool_the_other_readers_still_decide(tmp_path, monkeypatch):
    monkeypatch.setattr(rotation, "_which_exiftool", lambda: None)
    src = jpeg(tmp_path / "card" / "p.jpg", orientation=1)
    tmp = tmp_path / ".p.part"
    shutil.copyfile(src, tmp)
    out = rotation.apply_rotation(src, tmp, 1)
    assert out.status == "written" and out.checked_with == ["spuza", "pillow"]


def test_unsupported_formats_are_kept_not_written(tmp_path):
    src = tmp_path / "card" / "x.png"
    src.parent.mkdir()
    make_image().save(src)
    res, dst = copy_one(src, tmp_path / "out", 1)
    assert not res.rotated and ".png" in res.rotation_kept[0][1] and sha(dst) == sha(src)


# ---- hostile bytes -----------------------------------------------------------------------------------

def _tiff(entries: bytes, n: int, nxt: int = 0, bo: str = "<") -> bytes:
    head = (b"II*\x00" if bo == "<" else b"MM\x00*") + struct.pack(bo + "I", 8)
    return head + struct.pack(bo + "H", n) + entries + struct.pack(bo + "I", nxt)


def _entry(tag, typ, cnt, val, bo="<"):
    return struct.pack(bo + "HHI", tag, typ, cnt) + (struct.pack(bo + "HH", val, 0) if typ == 3 else struct.pack(bo + "I", val))


HOSTILE = {
    "empty": b"",
    "one byte": b"I",
    "header only": b"II*\x00",
    "ifd past end": b"II*\x00" + struct.pack("<I", 10**9),
    "huge entry count": b"II*\x00\x08\x00\x00\x00" + struct.pack("<H", 65535),
    "random": bytes(range(256)) * 8,
    "no tag": _tiff(_entry(0x0100, 3, 1, 5), 1),
    "wrong type": _tiff(_entry(274, 4, 1, 1), 1),
    "count two": _tiff(_entry(274, 3, 2, 1), 1),
    "value nine": _tiff(_entry(274, 3, 1, 9), 1),
    "value zero": _tiff(_entry(274, 3, 1, 0), 1),
    "subifd cycle": _tiff(_entry(274, 3, 1, 1) + _entry(330, 4, 1, 8), 2, 8),
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_dng_bytes_never_raise_and_keep_the_copy(tmp_path, name):
    src = tmp_path / "card" / "h.dng"
    src.parent.mkdir()
    src.write_bytes(HOSTILE[name])
    tmp = tmp_path / ".h.part"
    shutil.copyfile(src, tmp)
    out = rotation.apply_rotation(src, tmp, 1)
    if name == "subifd cycle":  # walked once, not forever; exiftool counts the loop twice, so with it on PATH: kept
        assert out.status == ("kept" if EXIFTOOL else "written")
        assert (tmp.read_bytes() == src.read_bytes()) == (out.status == "kept")
        return
    assert out.status == "kept" and out.reason
    assert tmp.read_bytes() == src.read_bytes()
    assert rotation.read_orientation(src) is None or name in ("value nine", "value zero")


def test_hostile_jpegs_never_raise(tmp_path):
    cases = {
        "soi only": b"\xff\xd8",
        "truncated segment": b"\xff\xd8\xff\xe1\x7f\xff" + b"Exif\x00\x00",
        "segment of length 1": b"\xff\xd8\xff\xe1\x00\x01",
        "not a marker": b"\xff\xd8\x00\x01\x02\x03",
        "garbage exif": b"\xff\xd8\xff\xe1\x00\x10Exif\x00\x00" + b"zz" * 4 + b"\xff\xd9",
        "many padding bytes": b"\xff\xd8" + b"\xff" * 5000 + b"\xff\xd9",
    }
    for name, data in cases.items():
        src = tmp_path / "card" / f"{name.replace(' ', '_')}.jpg"
        src.parent.mkdir(exist_ok=True)
        src.write_bytes(data)
        tmp = tmp_path / f".{src.name}.part"
        shutil.copyfile(src, tmp)
        out = rotation.apply_rotation(src, tmp, 2)
        assert out.status in ("kept", "written"), name
        if out.status == "kept":
            assert tmp.read_bytes() == data, name
        assert rotation.read_orientation(src) in (None, 1, 3, 6, 8), name


# ---- real camera files -------------------------------------------------------------------------------

REAL = [("R0045146.DNG", "dng"), ("20250704_X-Pro2_9268.RAF", "raf")]


@pytest.mark.parametrize("name,kind", REAL)
@pytest.mark.parametrize("turns", [1, 2, 3])
def test_real_raw_is_patched_in_place_and_nothing_else_moves(real_set, tmp_path, name, kind, turns):
    scratch = tmp_path / "card"
    scratch.mkdir()
    src = scratch / name
    shutil.copyfile(real_set / name, src)  # the "card" copy, so the read-only set is never at risk
    sha0, mt0 = sha(src), src.stat().st_mtime_ns
    res, dst = copy_one(src, tmp_path / "out", turns)
    assert res.rotated == [src], res.rotation_kept
    want = rotation.compose(1, turns)
    assert rotation.read_all_orientations(dst) == ([want, want] if kind == "raf" else [want])
    assert dst.stat().st_size == src.stat().st_size
    d = diff_offsets(src, dst)
    assert 1 <= len(d) <= (4 if kind == "raf" else 2)  # only the tag's own bytes differ
    assert sha(src) == sha0 and src.stat().st_mtime_ns == mt0
    assert Image.open(dst).getexif()[274] == want if kind == "dng" else True
    if HAS_RAWPY:
        with rawpy.imread(str(dst)) as raw:
            assert raw.sizes.flip == {1: 0, 6: 6, 3: 3, 8: 5}[want]
            thumb = raw.postprocess(half_size=True)
        assert (thumb.shape[1] < thumb.shape[0]) == (want in (6, 8))  # a quarter turn swaps the sides
    if EXIFTOOL:
        import subprocess
        out = subprocess.run([EXIFTOOL, "-a", "-n", "-s3", "-Orientation", str(dst)], capture_output=True, text=True)
        assert [int(x) for x in out.stdout.split()] == ([want, want] if kind == "raf" else [want])


@pytest.mark.parametrize("name,kind", REAL)
def test_real_raw_zero_turns_is_byte_identical(real_set, tmp_path, name, kind):
    src = tmp_path / "card" / name
    src.parent.mkdir()
    shutil.copyfile(real_set / name, src)
    _, dst = copy_one(src, tmp_path / "out", 0)
    assert sha(dst) == sha(src)


def test_real_dng_corrupted_tag_is_kept(real_set, tmp_path):
    src = tmp_path / "card" / "R0045146.DNG"
    src.parent.mkdir()
    shutil.copyfile(real_set / "R0045146.DNG", src)
    off = rotation.plan_patch(src, 1).edits[0][0]
    with open(src, "r+b") as f:  # the entry's type field sits 6 bytes before the value
        f.seek(off - 6)
        f.write(struct.pack("<H", 4))
    res, dst = copy_one(src, tmp_path / "out", 1)
    assert not res.rotated and res.rotation_kept and sha(dst) == sha(src)


def test_real_dng_rotate_twice_into_two_folders(real_set, tmp_path):
    src = tmp_path / "card" / "R0045147.DNG"
    src.parent.mkdir()
    shutil.copyfile(real_set / "R0045147.DNG", src)
    _, a = copy_one(src, tmp_path / "a", 1)
    _, b = copy_one(src, tmp_path / "b", 3)
    assert (rotation.read_orientation(a), rotation.read_orientation(b)) == (6, 8)


# ---- plan: a rotated copy already there is the same picture ------------------------------------------

def test_plan_skips_a_copy_that_already_holds_the_rotation_and_renames_a_plain_one(tmp_path):
    src = jpeg(tmp_path / "card" / "s.jpg", orientation=1)
    dest = tmp_path / "out"
    run_copy(plan_copy([[src]], dest, turns={src: 1}))
    plan = plan_copy([[src]], dest, turns={src: 1})
    assert [it.action for it in plan.items] == ["skip"]
    plan = plan_copy([[src]], dest)  # no rotation asked: the rotated file is not the original
    assert [it.action for it in plan.items] == ["rename"]
    plan = plan_copy([[src]], dest, turns={src: 2})
    assert [it.action for it in plan.items] == ["rename"]


# ---- the copy sheet ----------------------------------------------------------------------------------

def _card(tmp_path):
    card = tmp_path / "card"
    jpeg(card / "A.jpg", orientation=1)
    (card / "A.xmp").write_bytes(b"<x:xmpmeta/>")
    jpeg(card / "B.jpg", orientation=1)
    (card / "C.DNG").write_bytes(b"not really a dng" * 100)
    return card, scan(card)


def _run(sheet):
    done = []
    sheet.finished.connect(done.append)
    sheet.copy_button.click()
    assert wait_for(lambda: done)
    assert sheet.wait_idle()
    return done[0]


def test_sheet_writes_turns_to_images_not_companions_and_says_what_was_kept(qapp, tmp_path):
    card, pics = _card(tmp_path)
    by = {p.primary.name: p for p in pics}
    before = {p: sha(p) for p in card.iterdir()}
    sheet = CopySheet(pics, tmp_path / "out", organise=False,
                      rotations={by["A.jpg"].primary: 1, by["C.DNG"].primary: 2})
    res = _run(sheet)
    out = tmp_path / "out"
    assert rotation.read_orientation(out / "A.jpg") == 6 and rotation.read_orientation(out / "B.jpg") == 1
    assert sha(out / "A.xmp") == before[card / "A.xmp"]
    assert sha(out / "C.DNG") == before[card / "C.DNG"]
    assert [s.name for s, _ in res.rotation_kept] == ["C.DNG"]
    text = sheet.result_label.text()
    assert "rotation written to 1 file" in text and "kept the original orientation: C.DNG" in text
    assert {p: sha(p) for p in card.iterdir()} == before  # the card is not touched


def test_sheet_with_the_write_switched_off_writes_nothing(qapp, tmp_path):
    card, pics = _card(tmp_path)
    sheet = CopySheet(pics, tmp_path / "out", organise=False, write_rotation=False,
                      rotations={p.primary: 1 for p in pics})
    _run(sheet)
    assert "rotation" not in sheet.result_label.text()
    for p in (card / "A.jpg", card / "B.jpg"):
        assert sha(tmp_path / "out" / p.name) == sha(p)


def test_sheet_ignores_nonsense_rotations_and_none_means_nothing(qapp, tmp_path):
    card, pics = _card(tmp_path)
    sheet = CopySheet(pics, tmp_path / "out", organise=False,
                      rotations={pics[0].primary: "x", pics[1].primary: True, "nope": 3, None: 1})
    assert sheet._turns() == {}
    _run(sheet)
    assert "rotation" not in sheet.result_label.text()
    assert CopySheet(pics, tmp_path / "o2")._turns() == {}


def test_sheet_turns_go_to_a_raw_and_its_jpeg_twin(qapp, tmp_path):
    card = tmp_path / "card"
    jpeg(card / "P.jpg", orientation=1)
    (card / "P.RAF").write_bytes(b"raw")
    (card / "P.THM").write_bytes(b"thm")
    pics = scan(card)
    sheet = CopySheet(pics, tmp_path / "out", rotations={pics[0].primary: 3})
    assert sorted(f.name for f in sheet._turns()) == ["P.RAF", "P.jpg"] and set(sheet._turns().values()) == {3}


# ---- the session --------------------------------------------------------------------------------------

def _saved(tmp_path, n=6):
    root = make_resume_card(tmp_path, n)
    cat = open_card(root)
    return root, cat, SessionStore(tmp_path / "s"), card_key(root, cat.pictures)


def test_session_round_trip_keeps_rotations_beside_marks(tmp_path):
    root, cat, store, key = _saved(tmp_path)
    store.save(key, root, cat.pictures, {1, 2}, 3, rotations={0: 1, 4: 3, 5: 2})
    r = SessionStore(tmp_path / "s").load(key, root, cat.pictures)
    assert r.marks == {1, 2} and r.index == 3 and r.rotations == {0: 1, 4: 3, 5: 2}


def test_version_one_session_without_rotations_still_restores(tmp_path):
    root, cat, store, key = _saved(tmp_path)
    store.save(key, root, cat.pictures, {1}, 2, rotations={0: 1})
    path = store._path(key)
    doc = json.loads(path.read_text())
    doc["version"] = 1
    del doc["rotations"]
    path.write_text(json.dumps(doc))
    r = store.load(key, root, cat.pictures)
    assert r is not None and r.marks == {1} and r.index == 2 and r.rotations == {}
    doc["version"] = 3
    path.write_text(json.dumps(doc))
    assert store.load(key, root, cat.pictures) is None  # a newer file than we know is left alone
    assert session.VERSION == 2


def test_rotation_rows_are_validated_like_marks(tmp_path):
    root, cat, store, key = _saved(tmp_path)
    store.save(key, root, cat.pictures, {1}, 0, rotations={0: 1, 2: 2, 3: 3})
    path = store._path(key)
    doc = json.loads(path.read_text())
    rows = doc["rotations"]
    rows[0]["size"] += 1  # picture 0 changed since
    rows[1]["mtime"] += 3600  # picture 2 re-dated
    rows.append({"name": "gone.JPG", "size": 1, "mtime": 1.0, "turns": 1})
    rows.append({"name": rows[2]["name"], "size": rows[2]["size"], "mtime": rows[2]["mtime"], "turns": 7})
    path.write_text(json.dumps(doc))
    assert store.load(key, root, cat.pictures).rotations == {3: 3}


@pytest.mark.parametrize("junk", [None, 5, "x", {"a": 1}, [None, 3, "x", [], {"name": 1}, {"name": "a", "turns": "1"}], [[[[]]]]])
def test_hostile_rotations_field_restores_the_marks_anyway(tmp_path, junk):
    root, cat, store, key = _saved(tmp_path)
    store.save(key, root, cat.pictures, {1}, 2)
    path = store._path(key)
    doc = json.loads(path.read_text())
    doc["rotations"] = junk
    path.write_text(json.dumps(doc))
    r = store.load(key, root, cat.pictures)
    assert r is not None and r.marks == {1} and r.rotations == {}


def test_save_drops_out_of_range_and_wrong_typed_turns(tmp_path):
    root, cat, store, key = _saved(tmp_path)
    store.save(key, root, cat.pictures, {1}, 0, rotations={0: 0, 1: 4, 2: True, 3: "1", 99: 1, -1: 1, 4: 1})
    assert store.load(key, root, cat.pictures).rotations == {4: 1}


def test_only_rotations_is_worth_restoring(tmp_path):
    root, cat, store, key = _saved(tmp_path)
    store.save(key, root, cat.pictures, set(), 0, rotations={2: 1})
    assert store.load(key, root, cat.pictures).rotations == {2: 1}


def test_resume_controller_saves_and_restores_the_shared_dict(tmp_path, advanced):
    root = make_resume_card(tmp_path, 6)
    store = SessionStore(tmp_path / "s")
    cat = open_card(root)
    rot: dict[int, int] = {}
    c = ResumeController(cat, root, store, interval_ms=60000, rotations=rot)
    assert c.rotations is rot
    cat.set_mark(1, True)
    rot[2] = 1
    rot[4] = 3
    c.flush()

    cat2 = open_card(root)
    rot2 = {7: 2}  # whatever the window had is replaced by the card's own
    c2 = ResumeController(cat2, root, store, interval_ms=60000, rotations=rot2)
    banner = c2.restore()
    assert c2.rotations is rot2 and rot2 == {2: 1, 4: 3}
    assert "1 mark and 2 rotations and your place" in banner


def test_resume_change_detection_sees_a_rotation(tmp_path, advanced):
    root = make_resume_card(tmp_path, 4)
    store = SpyStore(tmp_path / "s")
    cat = open_card(root)
    rot: dict[int, int] = {}
    c = ResumeController(cat, root, store, interval_ms=60000, rotations=rot)
    c.tick()
    assert c.wait_idle() and store.calls == []  # opened and left alone: nothing written
    rot[1] = 2
    c.tick()
    assert c.wait_idle() and store.calls == ["save"]
    c.tick()
    assert c.wait_idle() and store.calls == ["save"]  # unchanged: no second write
    rot[1] = 3
    c.tick()
    assert c.wait_idle() and store.calls == ["save", "save"]
    del rot[1]
    c.tick()
    assert c.wait_idle() and len(store.calls) == 3


def test_start_fresh_keeps_the_rotations_on_disk_for_the_next_tick(tmp_path, advanced):
    root = make_resume_card(tmp_path, 4)
    store = SessionStore(tmp_path / "s")
    cat = open_card(root)
    rot = {2: 1}
    c = ResumeController(cat, root, store, interval_ms=60000, rotations=rot)
    cat.set_mark(0, True)
    c.flush()
    c.start_fresh()
    c.tick()
    assert c.wait_idle()
    cat2 = open_card(root)
    rot2: dict[int, int] = {}
    ResumeController(cat2, root, store, interval_ms=60000, rotations=rot2).restore()
    assert rot2 == {2: 1}


def test_resume_without_a_dict_still_works(tmp_path, advanced):
    root = make_resume_card(tmp_path, 4)
    c = ResumeController(open_card(root), root, SessionStore(tmp_path / "s"), interval_ms=60000)
    assert c.rotations == {} and c.restore() is None


def test_the_card_is_not_touched_by_saving_a_session(tmp_path):
    root, cat, store, key = _saved(tmp_path)
    before = {p: (sha(p), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    store.save(key, root, cat.pictures, {1}, 0, rotations={2: 1})
    assert before == {p: (sha(p), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    assert not any(str(store.folder) in str(p) for p in root.rglob("*"))
    assert os.path.commonpath([store.folder, root]) != str(root)
