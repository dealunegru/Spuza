"""Friend round 5: the story fit written on copy as <dest>/ed/<stem>_story.jpg (synthetic pictures only)."""

import hashlib
import threading
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from spuza import crop as crop_mod
from spuza import preview, story
from spuza.catalog import scan
from spuza.copy import STORY_BYTES, plan_copy, run_copy
from spuza.crop import CropJob
from spuza.story import Story, StoryJob
from spuza.ui.copy_sheet import CopySheet
from test_ui_copy_sheet import wait_for  # noqa: F401  (also makes the QApplication)

RED, GREEN, BLUE, YELLOW, BLACK = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 0, 0)


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "spuza-logs"))


def quadrants(w=300, h=200) -> Image.Image:
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


def card(tmp_path, names=("A",)):
    src = tmp_path / "card"
    src.mkdir()
    for n in names:
        save_jpg(src / f"{n}.JPG")
    return src, scan(src)


def sjob(p, st=Story(), turns=0):
    return StoryJob(p.primary, p.sidecar, st, turns)


def out_im(path):
    with Image.open(path) as im:
        im.load()
        return im.convert("RGB"), im.getexif()


# ---- pixels -----------------------------------------------------------------------------------

def test_fit_story_is_1080x1920_with_black_bands(tmp_path):
    src, pics = card(tmp_path)
    run_copy(plan_copy([p.files for p in pics], tmp_path / "out", True, stories=[sjob(pics[0])]))
    im, _ = out_im(tmp_path / "out" / "ed" / "A_story.jpg")
    assert im.size == (1080, 1920)
    assert near(im.getpixel((540, 100)), BLACK) and near(im.getpixel((540, 1850)), BLACK)
    assert near(im.getpixel((270, 800)), RED) and near(im.getpixel((810, 800)), GREEN)
    assert near(im.getpixel((270, 1100)), BLUE) and near(im.getpixel((810, 1100)), YELLOW)


def test_panned_zoomed_story_shows_the_expected_colour_at_the_centre(tmp_path):
    src, pics = card(tmp_path)
    st = Story(3.0, -0.25, 0.06)  # zoom is capped at 2.67 here; the picture moves left, so the frame sees the right half
    run_copy(plan_copy([p.files for p in pics], tmp_path / "out", True, stories=[sjob(pics[0], st)]))
    im, _ = out_im(tmp_path / "out" / "ed" / "A_story.jpg")
    assert im.size == (1080, 1920) and near(im.getpixel((540, 860)), GREEN)  # 100 px above the centre: off the seam
    run_copy(plan_copy([p.files for p in pics], tmp_path / "out2", True,
                       stories=[sjob(pics[0], Story(3.0, 0.25, 0.0))]))
    im2, _ = out_im(tmp_path / "out2" / "ed" / "A_story.jpg")
    assert near(im2.getpixel((540, 860)), RED)


def test_one_turn_is_baked_into_the_story(tmp_path):
    src, pics = card(tmp_path)
    run_copy(plan_copy([p.files for p in pics], tmp_path / "out", True, stories=[sjob(pics[0], Story(), 1)]))
    im, ex = out_im(tmp_path / "out" / "ed" / "A_story.jpg")
    assert im.size == (1080, 1920) and ex.get(274) is None
    assert near(im.getpixel((810, 500)), RED)  # top-left goes to top-right after a clockwise turn
    assert near(im.getpixel((270, 500)), BLUE) and near(im.getpixel((810, 1400)), GREEN)


def test_exif_oriented_picture_and_exif_carried_without_orientation(tmp_path):
    src = tmp_path / "card"
    src.mkdir()
    ex = Image.Exif()
    ex[274] = 6
    ex[306] = "2026:10:17 10:00:00"
    save_jpg(src / "O.JPG", quadrants(), exif=ex)  # stored 300x200, shown 200x300
    pics = scan(src)
    run_copy(plan_copy([p.files for p in pics], tmp_path / "out", True, stories=[sjob(pics[0])]))
    im, got = out_im(tmp_path / "out" / "ed" / "O_story.jpg")
    assert im.size == (1080, 1920) and got.get(274) is None
    assert got.get(306) == "2026:10:17 10:00:00"
    assert near(im.getpixel((810, 500)), RED)  # orientation 6 puts stored top-left at top-right


def test_story_of_a_raw_uses_the_full_decode_like_a_crop(tmp_path, monkeypatch):
    raw = tmp_path / "x.raf"
    raw.write_bytes(b"x")
    info = preview.PreviewInfo(raw, "raf-embedded", 30, 20, 1, 0.0)
    monkeypatch.setattr(preview, "load", lambda p, ms=2560: (Image.new("RGB", (30, 20)), info))
    monkeypatch.setattr(preview, "needs_full_decode", lambda p, i: True)
    monkeypatch.setattr(preview, "full_decode", lambda p, *a, **k: quadrants(600, 400))
    img, exif = story.export(StoryJob(raw, None, Story(), 0))
    assert img.size == (1080, 1920) and near(img.getpixel((270, 800)), RED) and exif


def test_best_pixels_matches_what_crop_uses(tmp_path):
    p = save_jpg(tmp_path / "a.jpg")
    im = crop_mod.best_pixels(p, None)
    assert im.size == (300, 200) and im.mode == "RGB" and im.info["exif"]
    with pytest.raises(crop_mod.CropError):
        crop_mod.best_pixels(tmp_path / "nope.jpg", None)


# ---- names, plan, run -----------------------------------------------------------------------------

@pytest.mark.parametrize("organise", [True, False])
def test_story_goes_to_ed_beside_untouched_original(tmp_path, organise):
    src, pics = card(tmp_path)
    before = sha(src / "A.JPG")
    dest = tmp_path / "out"
    plan = plan_copy([p.files for p in pics], dest, organise, stories=[sjob(pics[0])])
    assert plan.story_items[0].action == "story" and plan.story_items[0].size == STORY_BYTES
    res = run_copy(plan, log_path=tmp_path / "log.txt")
    assert (dest / "ed" / "A_story.jpg").is_file()
    assert res.stories == [pics[0].primary] and res.cropped == [] and not res.failed
    assert sha(dest / ("soc" if organise else ".") / "A.JPG") == before == sha(src / "A.JPG")
    assert not list(dest.rglob("*.spuza-part"))
    assert "story: 1 into ed" in res.log_path.read_text(encoding="utf-8")


def test_estimate_is_fixed_and_added(tmp_path):
    src, pics = card(tmp_path)
    a = plan_copy([p.files for p in pics], tmp_path / "o", True)
    b = plan_copy([p.files for p in pics], tmp_path / "o", True, stories=[sjob(pics[0])])
    assert b.total_bytes == a.total_bytes + STORY_BYTES and a.story_items == []


def test_name_clash_gets_a_suffix_and_nothing_is_overwritten(tmp_path):
    src, pics = card(tmp_path)
    dest = tmp_path / "out"
    (dest / "ed").mkdir(parents=True)
    keep = dest / "ed" / "A_story.jpg"
    keep.write_bytes(b"precious")
    plan = plan_copy([p.files for p in pics], dest, True, stories=[sjob(pics[0])])
    assert plan.story_items[0].dst == dest / "ed" / "A_story-1.jpg"
    run_copy(plan)
    assert keep.read_bytes() == b"precious" and (dest / "ed" / "A_story-1.jpg").is_file()
    plan2 = plan_copy([p.files for p in pics], dest, True, stories=[sjob(pics[0])])
    assert plan2.story_items[0].dst == dest / "ed" / "A_story-2.jpg"


def test_two_stories_with_one_stem_do_not_collide_and_crop_plus_story_both_write(tmp_path):
    a, b = tmp_path / "c1", tmp_path / "c2"
    for d in (a, b):
        d.mkdir()
        save_jpg(d / "IMG.JPG")
    groups = [[a / "IMG.JPG"], [b / "IMG.JPG"]]
    stories = [StoryJob(g[0], None, Story(), 0) for g in groups]
    crops = [CropJob(groups[0][0], None, (0, 0, 0.5, 0.5), 0)]
    plan = plan_copy(groups, tmp_path / "out", False, crops=crops, stories=stories)
    assert {i.dst.name for i in plan.story_items} == {"IMG_story.jpg", "IMG_story-1.jpg"}
    res = run_copy(plan)
    names = sorted(p.name for p in (tmp_path / "out" / "ed").iterdir())
    assert names == ["IMG.jpg", "IMG_story-1.jpg", "IMG_story.jpg"]
    assert len(res.stories) == 2 and len(res.cropped) == 1


def test_publish_race_renames_never_overwrites(tmp_path, monkeypatch):
    src, pics = card(tmp_path)
    dest = tmp_path / "out"
    plan = plan_copy([p.files for p in pics], dest, True, stories=[sjob(pics[0])])
    real = story.save

    def sneaky(img, dst, exif=None):
        real(img, dst, exif)
        (dest / "ed" / "A_story.jpg").write_bytes(b"appeared meanwhile")

    monkeypatch.setattr(story, "save", sneaky)
    res = run_copy(plan)
    assert (dest / "ed" / "A_story.jpg").read_bytes() == b"appeared meanwhile"
    assert (dest / "ed" / "A_story-1.jpg").is_file() and res.stories == [pics[0].primary]


def test_read_only_source_leaves_no_temp_and_keeps_the_time(tmp_path):
    import os
    import stat

    src, pics = card(tmp_path)
    os.utime(src / "A.JPG", (1_600_000_000, 1_600_000_000))
    os.chmod(src / "A.JPG", stat.S_IREAD)
    try:
        run_copy(plan_copy([p.files for p in pics], tmp_path / "out", True, stories=[sjob(pics[0])]))
    finally:
        os.chmod(src / "A.JPG", stat.S_IWRITE | stat.S_IREAD)
    ed = tmp_path / "out" / "ed"
    assert [x.name for x in ed.iterdir()] == ["A_story.jpg"]
    assert int((ed / "A_story.jpg").stat().st_mtime) == 1_600_000_000


def test_story_failure_lands_in_failed_and_leaves_no_temp(tmp_path):
    src, pics = card(tmp_path, ("A", "B"))
    plan = plan_copy([p.files for p in pics], tmp_path / "out", True, stories=[sjob(pics[0]), sjob(pics[1])])
    (src / "A.JPG").write_bytes(b"x" * 40)  # breaks after planning
    res = run_copy(plan)
    assert res.stories == [pics[1].primary]
    assert any(p == pics[0].primary and "story" in why.lower() for p, why in res.failed)
    assert not list((tmp_path / "out").rglob("*.spuza-part"))
    assert not (tmp_path / "out" / "ed" / "A_story.jpg").exists()


def test_cancel_between_items(tmp_path):
    src, pics = card(tmp_path, ("A", "B", "C"))
    plan = plan_copy([p.files for p in pics], tmp_path / "out", True, stories=[sjob(p) for p in pics])
    ev = threading.Event()

    def progress(done, total, path):
        if list((tmp_path / "out" / "ed").glob("*.jpg")):
            ev.set()

    res = run_copy(plan, progress, ev.is_set)
    assert res.cancelled and len(res.stories) == 1
    assert not list((tmp_path / "out").rglob("*.spuza-part"))


# ---- sheet ------------------------------------------------------------------------------------

def test_sheet_passes_stories_with_turns_and_reports(tmp_path):
    src, pics = card(tmp_path, ("A", "B"))
    sheet = CopySheet(pics, tmp_path / "out", stories={pics[0].primary: Story(), pics[1].primary: "garbage"},
                      rotations={pics[0].primary: 1})
    assert set(sheet.stories) == {pics[0].primary}
    assert "1 story fit" in sheet.plan_label.text()
    done = []
    sheet.finished.connect(done.append)
    sheet.copy_button.click()
    assert wait_for(lambda: done)
    sheet.wait_idle()
    out = tmp_path / "out" / "ed"
    assert (out / "A_story.jpg").is_file() and not (out / "B_story.jpg").exists()
    im, _ = out_im(out / "A_story.jpg")
    assert im.size == (1080, 1920) and near(im.getpixel((810, 500)), RED)  # the picture's turn is baked in
    assert "story 1 into ed" in sheet.result_label.text()
    assert "raw 0, soc 2" in sheet.result_label.text()
    QApplication.processEvents()


def test_sheet_without_stories_is_unchanged(tmp_path):
    src, pics = card(tmp_path)
    sheet = CopySheet(pics, tmp_path / "out")
    assert sheet.stories == {} and "story" not in sheet.plan_label.text()
    sheet2 = CopySheet(pics, tmp_path / "o2", stories={pics[0].primary: Story()})
    assert sheet2.plan.total_bytes == sheet.plan.total_bytes + STORY_BYTES
