"""Friend round 6: a crop's aspect ratio travels through the session, the copy sheet and the copy (synthetic data only)."""

import json
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from spuza import catalog, features, ratio
from spuza.catalog import scan
from spuza.copy import plan_copy, run_copy
from spuza.crop import CropJob
from spuza.ratio import FREE, Ratio
from spuza.session import SessionStore, card_key
from spuza.story import Story, StoryJob
from spuza.ui.copy_sheet import CopySheet
from spuza.ui.resume import ResumeController
from test_ui_copy_sheet import wait_for  # noqa: F401  (also makes the QApplication)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "spuza-logs"))
    monkeypatch.setenv("SPUZA_SESSIONS_DIR", str(tmp_path / "env-sessions"))


def card(tmp_path, names=("A",), size=(600, 400)):
    src = tmp_path / "card"
    src.mkdir()
    for n in names:
        Image.new("RGB", size, (200, 30, 30)).save(src / f"{n}.JPG", "JPEG", quality=92)
    return src, scan(src)


def cjob(p, r=None, turns=0, crop=(0.0, 0.0, 0.5, 0.5)):
    return CropJob(p.primary, p.sidecar, crop, turns, r)


def ed_names(plan):
    return [i.dst.name for i in plan.crop_items]


# ---- names ------------------------------------------------------------------------------------

# 6000x4000, entry -> (name unturned, name with turned=True); Free and Original carry no suffix
TABLE = {
    1: ("A.jpg", "A.jpg"),
    2: ("A.jpg", "A.jpg"),
    3: ("A_1x1.jpg", "A_1x1.jpg"),
    4: ("A_4x5.jpg", "A_5x4.jpg"),
    5: ("A_3x4.jpg", "A_4x3.jpg"),
    6: ("A_2x3.jpg", "A_3x2.jpg"),
    7: ("A_9x16.jpg", "A_16x9.jpg"),
    8: ("A_191x100.jpg", "A_100x191.jpg"),
}


def test_full_export_name_table_for_6000x4000(tmp_path):
    src, pics = card(tmp_path)
    p = pics[0]
    for entry, (plain, turned) in TABLE.items():
        for r, want in ((Ratio(entry), plain), (Ratio(entry, True), turned)):
            plan = plan_copy([p.files], tmp_path / f"o{entry}{r.turned}", True, crops=[cjob(p, r)])
            assert ed_names(plan) == [want], (entry, r)


def test_free_and_none_and_original_keep_the_plain_name(tmp_path):
    src, pics = card(tmp_path)
    p = pics[0]
    plan = plan_copy([p.files], tmp_path / "o", True, crops=[cjob(p), cjob(p, FREE), cjob(p, Ratio(2))])
    assert ed_names(plan) == ["A.jpg", "A-1.jpg", "A-2.jpg"]


@pytest.mark.parametrize("r,turns,want", [
    (Ratio(4), 0, "A_4x5.jpg"), (Ratio(4), 1, "A_5x4.jpg"), (Ratio(4), 2, "A_4x5.jpg"), (Ratio(4), 3, "A_5x4.jpg"),
    (Ratio(4, True), 1, "A_4x5.jpg"), (Ratio(8), 1, "A_100x191.jpg"), (Ratio(3), 1, "A_1x1.jpg"),
    (Ratio(1), 1, "A.jpg"), (Ratio(2), 3, "A.jpg"),
])
def test_odd_user_turns_name_the_on_screen_orientation(tmp_path, r, turns, want):
    src, pics = card(tmp_path)
    plan = plan_copy([pics[0].files], tmp_path / "o", True, crops=[cjob(pics[0], r, turns)])
    assert ed_names(plan) == [want]


def test_name_clash_adds_n_after_the_ratio_name(tmp_path):
    src, pics = card(tmp_path)
    dest = tmp_path / "out"
    (dest / "ed").mkdir(parents=True)
    (dest / "ed" / "A_4x5.jpg").write_bytes(b"precious")
    p = pics[0]
    plan = plan_copy([p.files], dest, True, crops=[cjob(p, Ratio(4)), cjob(p, Ratio(4)), cjob(p, Ratio(5))])
    assert ed_names(plan) == ["A_4x5-1.jpg", "A_4x5-2.jpg", "A_3x4.jpg"]


# ---- crop and story together --------------------------------------------------------------------

def test_a_picture_with_crop_and_story_writes_both(tmp_path):
    src, pics = card(tmp_path)
    p = pics[0]
    dest = tmp_path / "out"
    crop = ratio.largest_centred(0.8, 600, 400)
    plan = plan_copy([p.files], dest, True, crops=[cjob(p, Ratio(4), 0, crop)],
                     stories=[StoryJob(p.primary, p.sidecar, Story(), 0)])
    assert len(plan.crop_items) == 1 and len(plan.story_items) == 1
    res = run_copy(plan)
    ed = dest / "ed"
    assert sorted(f.name for f in ed.iterdir()) == ["A_4x5.jpg", "A_story.jpg"]
    with Image.open(ed / "A_4x5.jpg") as im:
        w, h = im.size
    assert abs(w - 320) <= 1 and abs(h - 400) <= 1
    with Image.open(ed / "A_story.jpg") as im:
        assert im.size == (1080, 1920)
    assert res.cropped == [p.primary] and res.stories == [p.primary] and not res.failed
    log = Path(res.log_path).read_text(encoding="utf-8")
    assert "cropped: 1 into ed" in log and "story: 1 into ed" in log


def test_turned_ratio_crop_is_written_on_screen_shaped(tmp_path):
    src, pics = card(tmp_path)
    p = pics[0]
    dest = tmp_path / "out"
    crop = ratio.largest_centred(0.8, 600, 400)  # 320x400 in the native frame
    run_copy(plan_copy([p.files], dest, True, crops=[cjob(p, Ratio(4), 1, crop)]))
    with Image.open(dest / "ed" / "A_5x4.jpg") as im:
        assert abs(im.size[0] - 400) <= 1 and abs(im.size[1] - 320) <= 1  # one quarter turn baked in


# ---- session ----------------------------------------------------------------------------------

def make_card(base: Path, n: int = 6) -> Path:
    root = base / "scard"
    (root / "DCIM").mkdir(parents=True)
    for i in range(n):
        (root / "DCIM" / f"IMG_{i:04d}.JPG").write_bytes(b"x" * (100 + i))
    return root


RECT = (0.1, 0.1, 0.6, 0.7)


def test_session_round_trip_with_ratios(tmp_path):
    root = make_card(tmp_path)
    pics = catalog.scan(root)
    store = SessionStore(tmp_path / "s")
    key = card_key(root, pics)
    store.save(key, root, pics, {1}, 2, crops={0: RECT, 1: RECT, 3: RECT},
               crop_ratios={0: Ratio(4), 1: Ratio(8, True), 3: FREE, 4: Ratio(5)})
    doc = json.loads((tmp_path / "s" / f"{key}.json").read_text())
    rows = {r["name"]: r for r in doc["crops"]}
    assert rows["DCIM/IMG_0000.JPG"]["ratio"] == "4" and rows["DCIM/IMG_0001.JPG"]["ratio"] == "8t"
    assert "ratio" not in rows["DCIM/IMG_0003.JPG"]  # Free: omitted
    r = store.load(key, root, pics)
    assert r.crops.keys() == {0, 1, 3}
    assert r.crop_ratios == {0: Ratio(4), 1: Ratio(8, True)}  # no ratio for 4: it has no crop


@pytest.mark.parametrize("bad", ["x", 9, True, [["4"]], None, "", "4tt", "9", [], {}, 4.5])
def test_garbage_ratio_keeps_the_crop_as_free(tmp_path, bad):
    root = make_card(tmp_path)
    pics = catalog.scan(root)
    store = SessionStore(tmp_path / "s")
    key = card_key(root, pics)
    store.save(key, root, pics, set(), 0, crops={0: RECT})
    path = tmp_path / "s" / f"{key}.json"
    doc = json.loads(path.read_text())
    doc["crops"][0]["ratio"] = bad
    path.write_text(json.dumps(doc))
    r = store.load(key, root, pics)
    assert r is not None and r.crops == {0: RECT} and r.crop_ratios == {}


def test_a_081_shaped_session_file_loads(tmp_path):
    root = make_card(tmp_path)
    pics = catalog.scan(root)
    store = SessionStore(tmp_path / "s")
    key = card_key(root, pics)
    p0 = pics[0]
    row = {"name": "DCIM/IMG_0000.JPG", "size": p0.size_bytes, "mtime": p0.mtime}
    doc = {"version": 2, "root": str(root), "saved_at": 1.0, "index": 0, "index_name": "DCIM/IMG_0000.JPG",
           "marks": [row], "rotations": [], "crops": [{**row, "rect": list(RECT)}],
           "stories": [{**row, "zoom": 1.5, "dx": 0.1, "dy": 0.0}]}
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / f"{key}.json").write_text(json.dumps(doc))
    r = store.load(key, root, pics)
    assert r.crops == {0: RECT} and r.crop_ratios == {} and r.stories == {0: (1.5, 0.1, 0.0)} and r.marks == {0}


def test_crop_and_story_both_restore(tmp_path):
    root = make_card(tmp_path)
    pics = catalog.scan(root)
    store = SessionStore(tmp_path / "s")
    key = card_key(root, pics)
    store.save(key, root, pics, set(), 0, crops={2: RECT}, stories={2: Story(2.0, 0.1, 0.1)},
               crop_ratios={2: Ratio(6)})
    r = store.load(key, root, pics)
    assert r.crops == {2: RECT} and r.stories == {2: (2.0, 0.1, 0.1)} and r.crop_ratios == {2: Ratio(6)}


# ---- ResumeController -------------------------------------------------------------------------

def open_card(root):
    return catalog.Catalog(catalog.scan(root))


def test_resume_saves_on_a_ratio_only_change_and_restores(tmp_path):
    features.set_mode(features.ADVANCED)
    root = make_card(tmp_path)
    store = SessionStore(tmp_path / "s")
    crops, ratios = {1: RECT}, {}
    c1 = ResumeController(open_card(root), root, store, interval_ms=60000, crops=crops, crop_ratios=ratios)
    c1.flush()
    assert c1.wait_idle()
    c1.tick()
    assert c1.wait_idle()
    ratios[1] = Ratio(4)  # nothing else changed
    c1.tick()
    assert c1.wait_idle()
    crops2, ratios2 = {}, {}
    c2 = ResumeController(open_card(root), root, store, interval_ms=60000, crops=crops2, crop_ratios=ratios2)
    assert c2.restore() is not None
    assert crops2 == {1: RECT} and ratios2 == {1: Ratio(4)}
    ratios2[1] = Ratio(4, True)
    c2.tick()
    assert c2.wait_idle()
    c3 = ResumeController(open_card(root), root, store, interval_ms=60000, crop_ratios=(r3 := {5: Ratio(3)}))
    c3.restore()
    assert r3 == {1: Ratio(4, True)}  # restore clears then fills the shared dict


def test_start_fresh_keeps_ratios_like_crops(tmp_path):
    features.set_mode(features.ADVANCED)
    root = make_card(tmp_path)
    store = SessionStore(tmp_path / "s")
    crops, ratios = {1: RECT}, {1: Ratio(7)}
    cat = open_card(root)
    c = ResumeController(cat, root, store, interval_ms=60000, crops=crops, crop_ratios=ratios)
    cat.set_mark(0, True)
    c.flush()
    c.start_fresh()
    assert ratios == {1: Ratio(7)} and crops == {1: RECT}
    c.tick()
    assert c.wait_idle()
    r = store.load(card_key(root, cat.pictures), root, cat.pictures)
    assert r.crop_ratios == {1: Ratio(7)}


# ---- copy sheet -------------------------------------------------------------------------------

def test_sheet_builds_crop_jobs_carrying_the_ratio(tmp_path):
    src, pics = card(tmp_path, ("A", "B", "C"))
    sheet = CopySheet(pics, tmp_path / "out", crops={p.primary: (0.0, 0.0, 0.5, 0.5) for p in pics},
                      crop_ratios={pics[0].primary: Ratio(4), pics[1].primary: "4:5", pics[2].primary: 7},
                      rotations={pics[0].primary: 1})
    assert sheet.crop_ratios == {pics[0].primary: Ratio(4)}  # non-Ratio values dropped
    jobs = {j.primary: j for j in sheet._crop_jobs()}
    assert jobs[pics[0].primary].ratio == Ratio(4) and jobs[pics[0].primary].turns == 1
    assert jobs[pics[1].primary].ratio is None and jobs[pics[2].primary].ratio is None
    assert sorted(i.dst.name for i in sheet.plan.crop_items) == ["A_5x4.jpg", "B.jpg", "C.jpg"]
    assert "3 cropped" in sheet.plan_label.text()


def test_sheet_copy_with_crop_and_story_counts_both(tmp_path):
    src, pics = card(tmp_path, ("A",))
    p = pics[0]
    sheet = CopySheet(pics, tmp_path / "out", crops={p.primary: ratio.largest_centred(0.8, 600, 400)},
                      crop_ratios={p.primary: Ratio(4)}, stories={p.primary: Story()})
    assert "1 cropped" in sheet.plan_label.text() and "1 story fit" in sheet.plan_label.text()
    done = []
    sheet.finished.connect(done.append)
    sheet.copy_button.click()
    assert wait_for(lambda: done)
    sheet.wait_idle()
    assert sorted(f.name for f in (tmp_path / "out" / "ed").iterdir()) == ["A_4x5.jpg", "A_story.jpg"]
    assert "cropped 1 into ed" in sheet.result_label.text() and "story 1 into ed" in sheet.result_label.text()
    QApplication.processEvents()
