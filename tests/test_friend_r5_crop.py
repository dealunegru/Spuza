"""Friend round 5: crop model, pixel pipeline and the ed copy (synthetic pictures only)."""

import hashlib
import math
import threading
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from spuza import crop as crop_mod
from spuza import preview
from spuza.catalog import scan
from spuza.copy import plan_copy, run_copy
from spuza.crop import CropJob
from spuza.ui.copy_sheet import CopySheet
from test_ui_copy_sheet import wait_for  # noqa: F401  (also makes the QApplication)

RED, GREEN, BLUE, YELLOW = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "spuza-logs"))


def quadrants(w=300, h=200) -> Image.Image:
    """Top-left red, top-right green, bottom-left blue, bottom-right yellow."""
    im = Image.new("RGB", (w, h))
    for (x0, y0, c) in ((0, 0, RED), (w // 2, 0, GREEN), (0, h // 2, BLUE), (w // 2, h // 2, YELLOW)):
        im.paste(c, (x0, y0, x0 + w // 2, y0 + h // 2))
    return im


def near(px, want, tol=40) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(px, want))


def save_jpg(path: Path, im=None, **kw) -> Path:
    (im or quadrants()).save(path, "JPEG", quality=95, **kw)
    return path


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---- model ------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, "abc", 5, (0, 0, 1), (0, 0, 1, 1, 1), (0, 0, float("nan"), 1),
                                 (0, 0, math.inf, 1), ("a", 0, 1, 1), (True, 0, 1, 1), [None] * 4, {}, object()])
def test_normalise_rejects_garbage(bad):
    assert crop_mod.normalise(bad) is None


def test_normalise_deeply_nested_input_is_one_clean_none():
    deep = []
    for _ in range(5000):
        deep = [deep]
    assert crop_mod.normalise(deep) is None
    assert crop_mod.normalise([deep, deep, deep, deep]) is None


def test_normalise_clamps_orders_and_rejects_slivers():
    assert crop_mod.normalise((-0.5, 0.1, 1.5, 0.9)) == (0.0, 0.1, 1.0, 0.9)
    assert crop_mod.normalise((0.8, 0.9, 0.2, 0.1)) == (0.2, 0.1, 0.8, 0.9)
    assert crop_mod.normalise([0.1, 0.1, 0.5, 0.5]) == (0.1, 0.1, 0.5, 0.5)
    assert crop_mod.normalise((0.1, 0.1, 0.105, 0.9)) is None  # 0.5 % wide
    assert crop_mod.normalise((0.1, 0.1, 0.9, 0.1)) is None
    assert crop_mod.normalise((2.0, 0.1, 3.0, 0.9)) is None  # clamps to nothing


def test_crop_box_maths():
    assert crop_mod.crop_box((300, 200), (0.0, 0.0, 0.5, 0.5)) == (0, 0, 150, 100)
    assert crop_mod.crop_box((300, 200), (0.5, 0.5, 1.0, 1.0)) == (150, 100, 300, 200)
    x0, y0, x1, y1 = crop_mod.crop_box((10, 10), (0.01, 0.01, 0.02, 0.02))
    assert x1 > x0 and y1 > y0  # never empty
    assert crop_mod.crop_box((300, 200), (0.0, 0.0, 1.0, 1.0)) == (0, 0, 300, 200)


# ---- pixels -----------------------------------------------------------------------------------

def test_render_crop_by_colour(tmp_path):
    p = save_jpg(tmp_path / "a.jpg")
    im = crop_mod.render(p, None, (0.5, 0.0, 1.0, 0.5), 0)
    assert im.size == (150, 100)
    assert near(im.getpixel((75, 50)), GREEN)


def test_render_applies_clockwise_turns_after_the_crop(tmp_path):
    p = save_jpg(tmp_path / "a.jpg")
    full = (0.0, 0.0, 1.0, 1.0)
    one = crop_mod.render(p, None, full, 1)
    assert one.size == (200, 300)
    assert near(one.getpixel((150, 50)), RED)  # top-left goes to top-right after a clockwise turn
    assert near(one.getpixel((50, 50)), BLUE)
    two = crop_mod.render(p, None, full, 2)
    assert near(two.getpixel((250, 150)), RED)
    three = crop_mod.render(p, None, full, 7)  # modulo 4
    assert near(three.getpixel((50, 250)), RED)
    c = crop_mod.render(p, None, (0.0, 0.0, 0.5, 1.0), 1)  # left half is 150x200, then turned
    assert c.size == (200, 150)


def test_exif_oriented_frame(tmp_path):
    """The crop fractions are in the EXIF-oriented picture: orientation 6 shows a 200x300 picture."""
    ex = Image.Exif()
    ex[274] = 6  # shown rotated 90 clockwise
    p = save_jpg(tmp_path / "o.jpg", quadrants(), exif=ex)  # stored 300x200
    full = crop_mod.render(p, None, (0, 0, 1, 1), 0)
    assert full.size == (200, 300)
    assert near(full.getpixel((150, 50)), RED)  # stored top-left shows at top-right
    top = crop_mod.render(p, None, (0.0, 0.0, 1.0, 0.5), 0)
    assert top.size == (200, 150)
    assert near(top.getpixel((50, 75)), BLUE) and near(top.getpixel((150, 75)), RED)


def test_write_orientation_one_exif_carried(tmp_path):
    ex = Image.Exif()
    ex[274] = 6
    ex[271] = "TestMaker"
    ex.get_ifd(0x8769)[36867] = "2024:05:06 07:08:09"
    p = save_jpg(tmp_path / "e.jpg", exif=ex)
    im = crop_mod.render(p, None, (0, 0, 0.5, 0.5), 1)
    out = tmp_path / "out.jpg"
    crop_mod.write_jpeg(im, out)
    with Image.open(out) as got:
        assert got.format == "JPEG"
        g = got.getexif()
        assert g.get(274) == 1
        assert g.get(271) == "TestMaker"
        assert g.get_ifd(0x8769).get(36867) == "2024:05:06 07:08:09"
        assert 0xA002 not in g.get_ifd(0x8769)  # stale pixel dimensions are not carried


def test_write_jpeg_without_exif(tmp_path):
    p = save_jpg(tmp_path / "n.jpg")
    out = tmp_path / "out.jpg"
    crop_mod.write_jpeg(crop_mod.render(p, None, (0, 0, 1, 1), 0), out)
    with Image.open(out) as got:
        assert got.size == (300, 200)


def test_png_primary_is_decoded_whole(tmp_path):
    p = tmp_path / "a.png"
    quadrants().save(p)
    im = crop_mod.render(p, None, (0.0, 0.5, 0.5, 1.0), 0)
    assert near(im.getpixel((10, 10)), BLUE)


def test_sidecar_preferred_over_raw(tmp_path, monkeypatch):
    raw = tmp_path / "x.raf"
    raw.write_bytes(b"not a raw")
    side = save_jpg(tmp_path / "x.jpg")
    monkeypatch.setattr(preview, "load", lambda *a, **k: pytest.fail("raw must not be read"))
    monkeypatch.setattr(preview, "full_decode", lambda *a, **k: pytest.fail("raw must not be read"))
    im = crop_mod.render(raw, side, (0, 0, 0.5, 0.5), 0)
    assert near(im.getpixel((5, 5)), RED) and im.size == (150, 100)


def test_raw_uses_native_preview_when_big_enough(tmp_path, monkeypatch):
    raw = tmp_path / "x.raf"
    raw.write_bytes(b"x")
    calls = []
    info = preview.PreviewInfo(raw, "raf-embedded", 300, 200, 1, 0.0)
    monkeypatch.setattr(preview, "load", lambda p, ms=2560: (quadrants(), info))
    monkeypatch.setattr(preview, "needs_full_decode", lambda p, i: False)
    monkeypatch.setattr(preview, "full_decode", lambda *a, **k: calls.append(1))
    im = crop_mod.render(raw, None, (0.5, 0.5, 1.0, 1.0), 0)
    assert near(im.getpixel((5, 5)), YELLOW) and not calls


def test_raw_with_small_preview_is_fully_decoded(tmp_path, monkeypatch):
    raw = tmp_path / "x.raf"
    raw.write_bytes(b"x")
    info = preview.PreviewInfo(raw, "raf-embedded", 30, 20, 1, 0.0)
    monkeypatch.setattr(preview, "load", lambda p, ms=2560: (Image.new("RGB", (30, 20)), info))
    monkeypatch.setattr(preview, "needs_full_decode", lambda p, i: True)
    monkeypatch.setattr(preview, "full_decode", lambda p, *a, **k: quadrants(600, 400))
    im = crop_mod.render(raw, None, (0, 0, 0.5, 0.5), 0)
    assert im.size == (300, 200) and near(im.getpixel((5, 5)), RED)


def test_failed_full_decode_falls_back_to_the_preview(tmp_path, monkeypatch):
    raw = tmp_path / "x.raf"
    raw.write_bytes(b"x")
    info = preview.PreviewInfo(raw, "raf-embedded", 300, 200, 1, 0.0)
    monkeypatch.setattr(preview, "load", lambda p, ms=2560: (quadrants(), info))
    monkeypatch.setattr(preview, "needs_full_decode", lambda p, i: True)

    def boom(*a, **k):
        raise preview.PreviewError("no rawpy")

    monkeypatch.setattr(preview, "full_decode", boom)
    assert crop_mod.render(raw, None, (0, 0, 1, 1), 0).size == (300, 200)


def test_unopenable_sidecar_falls_back_to_the_raw_rule(tmp_path, monkeypatch):
    raw = tmp_path / "x.raf"
    raw.write_bytes(b"x")
    side = tmp_path / "x.jpg"
    side.write_bytes(b"garbage")
    info = preview.PreviewInfo(raw, "raf-embedded", 300, 200, 1, 0.0)
    monkeypatch.setattr(preview, "load", lambda p, ms=2560: (quadrants(), info))
    monkeypatch.setattr(preview, "needs_full_decode", lambda p, i: False)
    assert crop_mod.render(raw, side, (0, 0, 1, 1), 0).size == (300, 200)


def test_render_errors_are_one_clean_error(tmp_path):
    with pytest.raises(crop_mod.CropError):
        crop_mod.render(tmp_path / "nope.jpg", None, (0, 0, 1, 1), 0)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"x" * 50)
    with pytest.raises(crop_mod.CropError):
        crop_mod.render(bad, None, (0, 0, 1, 1), 0)
    with pytest.raises(crop_mod.CropError):
        crop_mod.render(save_jpg(tmp_path / "ok.jpg"), None, None, 0)


# ---- copy -------------------------------------------------------------------------------------

def card(tmp_path, names=("A", "B")):
    src = tmp_path / "card"
    src.mkdir()
    for n in names:
        save_jpg(src / f"{n}.JPG")
    return src, scan(src)


def job(p, crop=(0.0, 0.0, 0.5, 0.5), turns=0):
    return CropJob(p.primary, p.sidecar, crop, turns)


@pytest.mark.parametrize("organise", [True, False])
def test_crop_goes_to_ed_beside_untouched_original(tmp_path, organise):
    src, pics = card(tmp_path, ("A",))
    before = sha(src / "A.JPG")
    dest = tmp_path / "out"
    plan = plan_copy([p.files for p in pics], dest, organise, crops=[job(pics[0])])
    res = run_copy(plan, log_path=tmp_path / "log.txt")
    ed = dest / "ed" / "A.jpg"
    assert ed.is_file() and res.cropped == [pics[0].primary] and not res.failed
    with Image.open(ed) as im:
        assert im.size == (150, 100)
    orig = dest / ("soc" if organise else ".") / "A.JPG"
    assert sha(orig) == before == sha(src / "A.JPG")
    assert not list(dest.rglob("*.spuza-part"))
    assert "cropped: 1 into ed" in res.log_path.read_text(encoding="utf-8")


def test_crop_turns_are_baked_even_without_rotation_patch(tmp_path):
    src, pics = card(tmp_path, ("A",))
    plan = plan_copy([p.files for p in pics], tmp_path / "out", True, crops=[job(pics[0], (0, 0, 1, 1), 1)])
    run_copy(plan)
    with Image.open(tmp_path / "out" / "ed" / "A.jpg") as out:
        assert out.size == (200, 300) and out.getexif().get(274) == 1


def test_originals_plan_is_unchanged_by_crops(tmp_path):
    src, pics = card(tmp_path)
    a = plan_copy([p.files for p in pics], tmp_path / "o1", True)
    b = plan_copy([p.files for p in pics], tmp_path / "o1", True, crops=[job(pics[0])])

    def strip(pl):
        return [(i.src, i.dst, i.size, i.action, i.turns) for i in pl.items]

    assert strip(a) == strip(b)
    assert b.total_bytes > a.total_bytes and len(b.crop_items) == 1 and a.crop_items == []


def test_name_taken_in_ed_gets_a_suffix_and_nothing_is_overwritten(tmp_path):
    src, pics = card(tmp_path, ("A",))
    dest = tmp_path / "out"
    (dest / "ed").mkdir(parents=True)
    keep = dest / "ed" / "A.jpg"
    keep.write_bytes(b"precious")
    plan = plan_copy([p.files for p in pics], dest, True, crops=[job(pics[0])])
    assert plan.crop_items[0].dst == dest / "ed" / "A-1.jpg"
    run_copy(plan)
    assert keep.read_bytes() == b"precious" and (dest / "ed" / "A-1.jpg").is_file()
    # a second run never skips: a fresh suffixed name each time
    plan2 = plan_copy([p.files for p in pics], dest, True, crops=[job(pics[0])])
    assert plan2.crop_items[0].dst == dest / "ed" / "A-2.jpg" and plan2.crop_items[0].action == "crop"


def test_two_crops_with_one_stem_in_one_plan_do_not_collide(tmp_path):
    a, b = tmp_path / "c1", tmp_path / "c2"
    for d in (a, b):
        d.mkdir()
        save_jpg(d / "IMG.JPG")
    groups = [[a / "IMG.JPG"], [b / "IMG.JPG"]]
    crops = [CropJob(g[0], None, (0, 0, 0.5, 0.5), 0) for g in groups]
    plan = plan_copy(groups, tmp_path / "out", False, crops=crops)
    assert {i.dst.name for i in plan.crop_items} == {"IMG.jpg", "IMG-1.jpg"}


def test_publish_race_renames_never_overwrites(tmp_path, monkeypatch):
    src, pics = card(tmp_path, ("A",))
    dest = tmp_path / "out"
    plan = plan_copy([p.files for p in pics], dest, True, crops=[job(pics[0])])
    real = crop_mod.write_jpeg

    def sneaky(im, dst, *a, **k):
        real(im, dst, *a, **k)
        (dest / "ed" / "A.jpg").write_bytes(b"appeared meanwhile")

    monkeypatch.setattr(crop_mod, "write_jpeg", sneaky)
    res = run_copy(plan)
    assert (dest / "ed" / "A.jpg").read_bytes() == b"appeared meanwhile"
    assert (dest / "ed" / "A-1.jpg").is_file() and res.cropped == [pics[0].primary]


def test_read_only_source_leaves_no_temp_and_keeps_the_time(tmp_path):
    import os
    import stat

    src, pics = card(tmp_path, ("A",))
    os.utime(src / "A.JPG", (1_600_000_000, 1_600_000_000))
    os.chmod(src / "A.JPG", stat.S_IREAD)
    try:
        plan = plan_copy([p.files for p in pics], tmp_path / "out", True, crops=[job(pics[0])])
        run_copy(plan)
    finally:
        os.chmod(src / "A.JPG", stat.S_IWRITE | stat.S_IREAD)
    ed = tmp_path / "out" / "ed"
    assert [x.name for x in ed.iterdir()] == ["A.jpg"]
    assert int((ed / "A.jpg").stat().st_mtime) == 1_600_000_000
    assert os.access(ed / "A.jpg", os.W_OK)


def test_crop_failure_lands_in_failed_and_leaves_no_temp(tmp_path):
    src, pics = card(tmp_path, ("A", "B"))
    plan = plan_copy([p.files for p in pics], tmp_path / "out", True, crops=[job(pics[0]), job(pics[1])])
    (src / "A.JPG").write_bytes(b"x" * 40)  # breaks after planning
    res = run_copy(plan)
    assert res.cropped == [pics[1].primary]
    assert any(p == pics[0].primary and "crop" in why.lower() for p, why in res.failed)
    assert not list((tmp_path / "out").rglob("*.spuza-part"))


def test_cancel_between_items(tmp_path):
    src, pics = card(tmp_path, ("A", "B", "C"))
    plan = plan_copy([p.files for p in pics], tmp_path / "out", True, crops=[job(p) for p in pics])
    ev = threading.Event()

    def progress(done, total, path):
        if list((tmp_path / "out" / "ed").glob("*.jpg")):  # first crop is published: stop
            ev.set()

    res = run_copy(plan, progress, ev.is_set)
    assert res.cancelled and len(res.cropped) == 1
    assert not list((tmp_path / "out").rglob("*.spuza-part"))


# ---- sheet ------------------------------------------------------------------------------------

def test_sheet_passes_crops_and_reports(tmp_path):
    src, pics = card(tmp_path, ("A", "B"))
    sheet = CopySheet(pics, tmp_path / "out", crops={pics[0].primary: (0.0, 0.0, 0.5, 0.5),
                                                      pics[1].primary: "garbage"})
    assert sheet.crops == {pics[0].primary: (0.0, 0.0, 0.5, 0.5)}
    assert "1 cropped" in sheet.plan_label.text()
    done = []
    sheet.finished.connect(done.append)
    sheet.copy_button.click()
    assert wait_for(lambda: done)
    sheet.wait_idle()
    assert (tmp_path / "out" / "ed" / "A.jpg").is_file() and not (tmp_path / "out" / "ed" / "B.jpg").exists()
    assert "cropped 1 into ed" in sheet.result_label.text()
    assert "raw 0, soc 2" in sheet.result_label.text()  # crops do not count as originals
    QApplication.processEvents()


def test_sheet_without_crops_is_unchanged(tmp_path):
    src, pics = card(tmp_path, ("A",))
    sheet = CopySheet(pics, tmp_path / "out")
    assert sheet.crops == {} and "cropped" not in sheet.plan_label.text()


def test_sheet_estimate_counts_crops(tmp_path):
    src, pics = card(tmp_path, ("A",))
    plain = CopySheet(pics, tmp_path / "o1")
    cropped = CopySheet(pics, tmp_path / "o2", crops={pics[0].primary: (0, 0, 1, 1)})
    assert cropped.plan.total_bytes > plain.plan.total_bytes
