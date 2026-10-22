"""Task spp-copy: the copy writes each picture's pills into its destination files (XMP embedded or sidecar)."""

import hashlib
import os
import random
from pathlib import Path

import pytest
from PIL import Image

from spuza import copy as copy_mod
from spuza import finder_tags, rotation, xmp
from spuza.copy import plan_copy, run_copy
from spuza.xmp import PillTag

HERO = PillTag("Hero", "#2fb4e8")
DELIVERED = PillTag("Delivered", "#8fd14f")
PILLS = (HERO, DELIVERED)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(finder_tags, "write_tags", lambda path, pills: False)  # never touch xattrs from a test


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def make_image(w=24, h=16) -> Image.Image:
    im = Image.new("RGB", (w, h))
    im.putdata([(x * 10 % 256, y * 15 % 256, (x + y) % 256) for y in range(h) for x in range(w)])
    return im


def jpeg(path: Path, orientation: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if orientation is None:
        make_image().save(path, "JPEG", quality=90)
    else:
        ex = Image.Exif()
        ex[0x0112] = orientation
        make_image().save(path, "JPEG", quality=90, exif=ex.tobytes())
    return path


def png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    make_image().save(path, "PNG")
    return path


def raf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"FUJIFILMCCD-RAW " + random.Random(7).randbytes(5000))
    return path


def pills_of(path: Path):
    blob = xmp.read_embedded(path)
    return xmp.parse_pills(blob) if blob else None


def pixels(path: Path):
    with Image.open(path) as im:
        return im.size, im.mode, im.tobytes()


def test_jpeg_copy_carries_pills_and_pixels(tmp_path):
    src = jpeg(tmp_path / "card" / "A.JPG")
    before = sha(src)
    plan = plan_copy([[src]], tmp_path / "out", pills={src: PILLS})
    assert plan.items[0].pills == PILLS
    res = run_copy(plan)
    dst = tmp_path / "out" / "A.JPG"
    assert not res.failed and res.pilled == [src] and res.pills_kept == []
    assert pills_of(dst) == list(PILLS)
    assert pixels(dst) == pixels(src)
    assert sha(src) == before
    assert not list((tmp_path / "out").glob("*.xmp"))  # embedded: no sidecar
    assert not list((tmp_path / "out").glob(".*spuza-part*"))
    assert any(line.startswith("PILLS  ") and "embedded" in line for line in res.log_path.read_text("utf-8").splitlines())


def test_png_copy_carries_pills(tmp_path):
    src = png(tmp_path / "card" / "A.PNG")
    res = run_copy(plan_copy([[src]], tmp_path / "out", pills={src: PILLS}))
    dst = tmp_path / "out" / "A.PNG"
    assert res.pilled == [src] and not res.failed
    assert pills_of(dst) == list(PILLS)
    assert pixels(dst) == pixels(src)


def test_raw_is_byte_identical_and_gets_sidecar_jpeg_twin_embeds(tmp_path):
    card = tmp_path / "card"
    r, j = raf(card / "X1.RAF"), jpeg(card / "X1.JPG")
    before = {p: sha(p) for p in (r, j)}
    res = run_copy(plan_copy([[r, j]], tmp_path / "out", pills={r: PILLS, j: PILLS}))
    out = tmp_path / "out"
    assert not res.failed and sorted(res.pilled) == sorted([r, j])
    assert sha(out / "X1.RAF") == before[r]  # a RAW is never rewritten
    side = out / "X1.xmp"
    assert xmp.parse_pills(side.read_bytes()) == list(PILLS)
    assert pills_of(out / "X1.JPG") == list(PILLS)  # the JPEG carries the packet too (pair decision)
    assert {p: sha(p) for p in (r, j)} == before


def test_card_xmp_companion_is_merged_into_the_copy_only(tmp_path):
    card = tmp_path / "card"
    r = raf(card / "X1.RAF")
    foreign = (
        b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        b'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="CameraX">'
        b'<dc:subject><rdf:Bag><rdf:li>FromCamera</rdf:li></rdf:Bag></dc:subject>'
        b'</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    )
    comp = card / "X1.xmp"
    comp.write_bytes(foreign)
    before = {p: sha(p) for p in (r, comp)}
    # companion LAST in the group: the sidecar merge must still wait for its published copy
    for n, group in enumerate(([r, comp], [comp, r])):
        out = tmp_path / f"out{n}"
        res = run_copy(plan_copy([group], out, pills={r: PILLS}))
        assert not res.failed and res.pilled == [r]
        got = xmp.parse_pills((out / "X1.xmp").read_bytes())
        assert got == list(PILLS)
        merged = (out / "X1.xmp").read_bytes()
        assert b"FromCamera" in merged and b"CameraX" in merged  # the foreign keyword and tool survive the merge
        assert b"Hero" in merged
        assert {p: sha(p) for p in (r, comp)} == before  # the card is never written (dec-053)
        assert sha(out / "X1.RAF") == before[r]


def test_rotation_and_pills_on_the_same_jpeg(tmp_path):
    src = jpeg(tmp_path / "card" / "A.JPG", orientation=1)
    plan = plan_copy([[src]], tmp_path / "out", turns={src: 1}, pills={src: PILLS})
    res = run_copy(plan)
    dst = tmp_path / "out" / "A.JPG"
    assert not res.failed and res.rotated == [src] and res.pilled == [src]
    assert rotation.read_orientation(dst) == 6
    assert pills_of(dst) == list(PILLS)
    with Image.open(dst) as a, Image.open(src) as b:
        assert a.tobytes() == b.tobytes()  # pixels unchanged: only the orientation tag and the packet differ


def test_renamed_group_gets_a_matching_sidecar(tmp_path):
    card = tmp_path / "card"
    r = raf(card / "X1.RAF")
    out = tmp_path / "out"
    out.mkdir()
    (out / "X1.RAF").write_bytes(b"something else")  # a different file holds the name: the group is renamed
    res = run_copy(plan_copy([[r]], out, pills={r: PILLS}))
    assert not res.failed and res.pilled == [r]
    # the copy's own rename pattern is "-N"; the sidecar follows whatever final name the primary got
    assert (out / "X1-1.RAF").exists()
    assert xmp.parse_pills((out / "X1-1.xmp").read_bytes()) == list(PILLS)
    assert not (out / "X1.xmp").exists()


def test_recopy_skips_and_leaves_pills_unchanged(tmp_path):
    card = tmp_path / "card"
    r, j = raf(card / "X1.RAF"), jpeg(card / "X1.JPG")
    k = jpeg(card / "Y.JPG")
    pills = {r: PILLS, j: PILLS, k: (HERO,)}
    out = tmp_path / "out"
    run_copy(plan_copy([[r, j], [k]], out, pills=pills))
    names = sorted(p.name for p in out.iterdir())
    snap = {p.name: sha(p) for p in out.iterdir()}
    plan2 = plan_copy([[r, j], [k]], out, pills=pills)
    assert [it.action for it in plan2.items] == ["skip", "skip", "skip"]
    res2 = run_copy(plan2)
    assert not res2.failed and res2.copied == []
    assert sorted(p.name for p in out.iterdir()) == names  # no _1 / -1 files
    assert {p.name: sha(p) for p in out.iterdir()} == snap
    assert sorted(res2.pilled) == sorted([r, j, k])


def test_skipped_raw_gets_its_changed_pills_merged(tmp_path):
    r = raf(tmp_path / "card" / "X1.RAF")
    out = tmp_path / "out"
    run_copy(plan_copy([[r]], out, pills={r: (HERO,)}))
    plan2 = plan_copy([[r]], out, pills={r: PILLS})
    assert plan2.items[0].action == "skip"
    res = run_copy(plan2)
    assert res.pilled == [r]
    assert xmp.parse_pills((out / "X1.xmp").read_bytes()) == list(PILLS)
    assert not any("-1" in p.name or "_1" in p.name for p in out.iterdir())


def test_declined_embed_falls_back_to_sidecar(tmp_path, monkeypatch):
    src = jpeg(tmp_path / "card" / "A.JPG")
    monkeypatch.setattr(xmp, "apply_embedded", lambda tmp, pills: xmp.Outcome("declined", "forced"))
    res = run_copy(plan_copy([[src]], tmp_path / "out", pills={src: PILLS}))
    dst = tmp_path / "out" / "A.JPG"
    assert not res.failed and res.pilled == [src]
    assert sha(dst) == sha(src)  # untouched bytes
    assert xmp.parse_pills((tmp_path / "out" / "A.xmp").read_bytes()) == list(PILLS)
    assert "sidecar" in res.log_path.read_text("utf-8")


def test_nothing_written_is_recorded_in_pills_kept(tmp_path, monkeypatch):
    r = raf(tmp_path / "card" / "X1.RAF")
    monkeypatch.setattr(xmp, "write_sidecar", lambda path, pills: xmp.Outcome("declined", "no room"))
    res = run_copy(plan_copy([[r]], tmp_path / "out", pills={r: PILLS}))
    assert not res.failed and res.copied == [r] and res.pilled == []
    assert res.pills_kept == [(r, "no room")]
    assert "PILLS? " in res.log_path.read_text("utf-8")


@pytest.mark.parametrize("how", ["false", "raises"])
def test_finder_tag_failure_never_fails_the_item(tmp_path, monkeypatch, how):
    src = jpeg(tmp_path / "card" / "A.JPG")

    def tags(path, pills):
        if how == "raises":
            raise OSError("xattr refused")
        return False

    monkeypatch.setattr(finder_tags, "write_tags", tags)
    res = run_copy(plan_copy([[src]], tmp_path / "out", pills={src: PILLS}))
    assert not res.failed and res.copied == [src] and res.tagged == []
    assert pills_of(tmp_path / "out" / "A.JPG") == list(PILLS)


def test_finder_tags_are_recorded_and_use_the_final_path(tmp_path, monkeypatch):
    src = jpeg(tmp_path / "card" / "A.JPG")
    seen = []
    monkeypatch.setattr(finder_tags, "write_tags", lambda path, pills: seen.append((Path(path), tuple(pills))) or True)
    out = tmp_path / "out"
    out.mkdir()
    (out / "A.JPG").write_bytes(b"other")
    res = run_copy(plan_copy([[src]], out, pills={src: PILLS}))
    assert res.tagged == [src]
    assert seen == [(out / "A-1.JPG", PILLS)]


def test_no_pills_means_byte_identical_copies_as_before(tmp_path):
    card = tmp_path / "card"
    files = [jpeg(card / "A.JPG"), png(card / "B.PNG"), raf(card / "X.RAF")]
    plan = plan_copy([[f] for f in files], tmp_path / "out")
    assert all(it.pills == () for it in plan.items)
    res = run_copy(plan)
    assert not res.failed and res.pilled == [] and res.pills_kept == [] and res.tagged == []
    for f in files:
        assert sha(tmp_path / "out" / f.name) == sha(f)
    assert not list((tmp_path / "out").glob("*.xmp"))
    assert not list((tmp_path / "out").glob(".*spuza-part*"))


def test_pills_are_not_set_on_companions(tmp_path):
    card = tmp_path / "card"
    r = raf(card / "X1.RAF")
    comp = card / "X1.xmp"
    comp.write_bytes(b"<x/>")
    plan = plan_copy([[r, comp]], tmp_path / "out", pills={r: PILLS})
    assert [it.pills for it in plan.items] == [PILLS, ()]


def test_planned_bytes_match_what_run_copy_writes(tmp_path):
    src = jpeg(tmp_path / "card" / "A.JPG", orientation=1)
    run_copy(plan_copy([[src]], tmp_path / "out", turns={src: 3}, pills={src: PILLS}))
    assert copy_mod._same_planned(src, tmp_path / "out" / "A.JPG", 3, PILLS)
    # other pills differ only in Spuza's own entries: still "the same" (run_copy re-embeds the current ones)
    assert copy_mod._same_planned(src, tmp_path / "out" / "A.JPG", 3, (HERO,))
    assert not copy_mod._same_planned(src, tmp_path / "out" / "A.JPG", 0, PILLS)


def test_crop_output_carries_the_pills(tmp_path):
    from spuza import crop as crop_mod

    src = jpeg(tmp_path / "card" / "A.JPG")
    job = crop_mod.CropJob(src, None, (0.0, 0.0, 0.5, 0.5), 0, None)
    out = tmp_path / "out"
    res = run_copy(plan_copy([[src]], out, organise=True, crops=[job], pills={src: PILLS}))
    assert not res.failed and res.cropped == [src]
    made = list((out / "ed").glob("*.jpg"))
    assert len(made) == 1 and pills_of(made[0]) == list(PILLS)
    assert os.listdir(out / "ed") == [made[0].name]


FOREIGN = (
    b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    b'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="CameraX">'
    b'<dc:subject><rdf:Bag><rdf:li>FromCamera</rdf:li></rdf:Bag></dc:subject>'
    b'</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
)


def trio(tmp_path):
    card = tmp_path / "card"
    r, j = raf(card / "X1.RAF"), jpeg(card / "X1.JPG")
    comp = card / "X1.xmp"
    comp.write_bytes(FOREIGN)
    return r, j, comp


@pytest.mark.parametrize("organise", [False, True])
def test_recopy_with_a_card_xmp_companion_skips(tmp_path, organise):
    r, j, comp = trio(tmp_path)
    group = [r, j, comp]
    pills = {r: PILLS, j: PILLS}
    out = tmp_path / "out"
    run_copy(plan_copy([group], out, organise=organise, pills=pills))
    names = sorted(str(p.relative_to(out)) for p in out.rglob("*"))
    snap = {str(p.relative_to(out)): sha(p) for p in out.rglob("*") if p.is_file()}
    plan2 = plan_copy([group], out, organise=organise, pills=pills)
    assert [it.action for it in plan2.items] == ["skip", "skip", "skip"]
    res2 = run_copy(plan2)
    assert not res2.failed and not res2.pills_kept
    assert sorted(str(p.relative_to(out)) for p in out.rglob("*")) == names
    assert {str(p.relative_to(out)): sha(p) for p in out.rglob("*") if p.is_file()} == snap
    assert not list(out.rglob(".*spuza-part*"))


@pytest.mark.parametrize("organise", [False, True])
def test_recopy_with_changed_pills_skips_and_updates_the_copies(tmp_path, organise):
    r, j, comp = trio(tmp_path)
    group = [r, j, comp]
    out = tmp_path / "out"
    run_copy(plan_copy([group], out, organise=organise, pills={r: (HERO,), j: (HERO,)}))
    new = {r: PILLS, j: PILLS}
    plan2 = plan_copy([group], out, organise=organise, pills=new)
    assert [it.action for it in plan2.items] == ["skip", "skip", "skip"]
    res2 = run_copy(plan2)
    assert not res2.failed and sorted(res2.pilled) == sorted([r, j])
    assert not [p for p in out.rglob("*") if "-1" in p.name or "_1" in p.name]
    jd = next(out.rglob("X1.JPG"))
    assert pills_of(jd) == list(PILLS)
    assert pixels(jd) == pixels(j)
    side = next(out.rglob("X1.xmp"))
    assert xmp.parse_pills(side.read_bytes()) == list(PILLS)
    assert b"FromCamera" in side.read_bytes()
    assert sha(next(out.rglob("X1.RAF"))) == sha(r)
    assert not list(out.rglob(".*spuza-part*"))


def test_a_different_foreign_companion_still_renames(tmp_path):
    r, j, comp = trio(tmp_path)
    group = [r, comp]
    out = tmp_path / "out"
    out.mkdir()
    (out / "X1.xmp").write_bytes(FOREIGN.replace(b"FromCamera", b"Someone else"))
    plan = plan_copy([group], out, pills={r: PILLS})
    assert all(it.action == "rename" for it in plan.items)


def test_unparseable_destination_is_not_identical(tmp_path):
    r, j, comp = trio(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "X1.xmp").write_bytes(b"not xml at all")
    assert not copy_mod._same_planned(comp, out / "X1.xmp", 0, (), companion=True)


def test_far_off_size_is_rejected_without_reading(tmp_path, monkeypatch):
    src = jpeg(tmp_path / "card" / "A.JPG")
    dst = tmp_path / "out" / "A.JPG"
    dst.parent.mkdir()
    dst.write_bytes(src.read_bytes() + b"\x00" * (copy_mod.XMP_SLACK + 10))
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("read a file"))
    assert copy_mod._same_planned(src, dst, 0, PILLS) is False
