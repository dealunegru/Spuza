"""Friend round 7: derived pictures travel through the session, the resume controller and the copy sheet
(synthetic data only, everything under tmp_path)."""

import json

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from spuza import catalog, features
from spuza.catalog import scan
from spuza.derived import CROP, STORY, Band, Derived, export_tail, to_row
from spuza.ratio import Ratio
from spuza.session import SessionStore, card_key
from spuza.story import Story
from spuza.ui.copy_sheet import CopySheet
from spuza.ui.resume import ResumeController
from test_ui_copy_sheet import wait_for  # noqa: F401  (also makes the QApplication)

RECT = (0.0, 0.0, 0.5, 0.5)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "spuza-logs"))
    monkeypatch.setenv("SPUZA_SESSIONS_DIR", str(tmp_path / "env-sessions"))


def card(tmp_path, names=("A", "B", "C"), size=(600, 400)):
    src = tmp_path / "card"
    src.mkdir()
    for n in names:
        Image.new("RGB", size, (200, 30, 30)).save(src / f"{n}.JPG", "JPEG", quality=92)
    return src, scan(src)


def three_derived():
    return [
        Derived(1, 0, CROP, crop=RECT, ratio=Ratio(4), marked=True),
        Derived(2, 0, STORY, story=Story(1.5, 0.1, -0.2), marked=False),
        Derived(3, 1, CROP, crop=(0.1, 0.1, 0.9, 0.9), ratio=None, marked=True),
    ]


# ---- session ----------------------------------------------------------------------------------

def test_round_trip_of_three_derived_on_two_pictures(tmp_path):
    src, pics = card(tmp_path)
    store = SessionStore(tmp_path / "s")
    key = card_key(src, pics)
    store.save(key, src, pics, {0}, 1, derived=three_derived())
    doc = json.loads((tmp_path / "s" / f"{key}.json").read_text())
    assert [r["id"] for r in doc["derived"]] == [1, 2, 3]
    assert doc["derived"][0]["name"] == "A.JPG" and doc["derived"][2]["name"] == "B.JPG"
    assert doc["derived"][0]["size"] == pics[0].size_bytes and doc["version"] == 2
    r = store.load(key, src, pics)
    assert r.derived == tuple(three_derived())
    assert [d.marked for d in r.derived] == [True, False, True]
    assert r.derived[0].ratio == Ratio(4) and r.derived[2].ratio is None
    assert r.derived[1].story == Story(1.5, 0.1, -0.2)
    assert r.marks == frozenset({0})


def test_only_derived_is_worth_restoring(tmp_path):
    src, pics = card(tmp_path)
    store = SessionStore(tmp_path / "s")
    key = card_key(src, pics)
    store.save(key, src, pics, set(), 0, derived=three_derived()[:1])
    r = store.load(key, src, pics)
    assert r is not None and len(r.derived) == 1


def test_a_090_doc_loads_as_derived_with_marks_from_the_original(tmp_path):
    src, pics = card(tmp_path)
    store = SessionStore(tmp_path / "s")
    key = card_key(src, pics)
    store.save(key, src, pics, {0}, 0, crops={0: RECT, 2: RECT}, crop_ratios={0: Ratio(4)},
               stories={0: Story(2.0, 0.0, 0.5), 2: Story()})
    doc = json.loads((tmp_path / "s" / f"{key}.json").read_text())
    assert "derived" not in doc  # what 0.9.0 wrote
    r = store.load(key, src, pics)
    assert [(d.id, d.source, d.kind, d.marked) for d in r.derived] == [
        (1, 0, CROP, True), (2, 0, STORY, True), (3, 2, CROP, False), (4, 2, STORY, False)]
    assert r.derived[0].ratio == Ratio(4) and r.derived[2].ratio is None
    assert r.derived[1].story == Story(2.0, 0.0, 0.5)
    assert r.crops == {0: RECT, 2: RECT} and r.crop_ratios == {0: Ratio(4)} and set(r.stories) == {0, 2}


def test_garbage_derived_rows_are_dropped_and_counted(tmp_path):
    src, pics = card(tmp_path)
    store = SessionStore(tmp_path / "s")
    key = card_key(src, pics)
    store.save(key, src, pics, {0}, 0, derived=three_derived()[:1])
    path = tmp_path / "s" / f"{key}.json"
    doc = json.loads(path.read_text())
    good = doc["derived"][0]
    ident = {k: good[k] for k in ("name", "size", "mtime")}
    doc["derived"] += [
        {**ident, "id": 2, "kind": "bogus"},
        {**ident, "id": 3, "kind": "crop", "rect": [0, 0, "x", 1]},
        {**ident, "id": 4, "kind": "story", "zoom": float("nan"), "dx": 0, "dy": 0},
        {**ident, "id": 5, "kind": "crop", "rect": [0, 0, 1, 1], "name": "gone.JPG"},
        {**ident, "id": 6, "kind": "crop", "rect": [0, 0, 1, 1], "size": 1},
        "junk", 7, None, [], {"name": 5},
    ]
    path.write_text(json.dumps(doc, allow_nan=True))
    r = store.load(key, src, pics)
    assert [d.id for d in r.derived] == [1]
    assert r.dropped >= 2
    doc["derived"] = "not a list"
    path.write_text(json.dumps(doc))
    assert store.load(key, src, pics).derived == ()


def test_deeply_nested_derived_row_does_not_crash_load(tmp_path):
    src, pics = card(tmp_path)
    store = SessionStore(tmp_path / "s")
    key = card_key(src, pics)
    store.save(key, src, pics, {0}, 0, derived=three_derived()[:1])
    path = tmp_path / "s" / f"{key}.json"
    text = path.read_text()
    deep = "[" * 3000 + "]" * 3000
    path.write_text(text.replace('"derived":[', '"derived":[{"name":"A.JPG","id":9,"kind":"crop","rect":' + deep + "},", 1))
    r = store.load(key, src, pics)
    assert r is None or [d.id for d in r.derived] in ([1], [])


def test_session_without_derived_has_empty_tuple(tmp_path):
    src, pics = card(tmp_path)
    store = SessionStore(tmp_path / "s")
    key = card_key(src, pics)
    store.save(key, src, pics, {1}, 0)
    assert store.load(key, src, pics).derived == ()


# ---- resume controller ------------------------------------------------------------------------

def open_card(root):
    return catalog.Catalog(catalog.scan(root))


def test_resume_saves_when_only_the_band_changed_and_restores_into_the_shared_band(tmp_path):
    features.set_mode(features.ADVANCED)
    root, _ = card(tmp_path)
    store = SessionStore(tmp_path / "s")
    band = Band()
    c1 = ResumeController(open_card(root), root, store, interval_ms=60000, band=band)
    c1.flush()
    assert c1.wait_idle()
    c1.tick()
    assert c1.wait_idle()
    band.add(0, CROP, crop=RECT, ratio=Ratio(4))
    band.add(1, STORY, story=Story(), marked=False)
    c1.tick()
    assert c1.wait_idle()
    band2 = Band()
    c2 = ResumeController(open_card(root), root, store, interval_ms=60000, band=band2)
    assert c2.restore() is not None
    assert band2.items == band.items and len(band2) == 2
    band2.set_marked(band2.items[1].id, True)  # a mark alone is a change
    c2.tick()
    assert c2.wait_idle()
    band3 = Band([Derived(9, 2, CROP, crop=RECT)])
    c3 = ResumeController(open_card(root), root, store, interval_ms=60000, band=band3)
    c3.restore()
    assert [(d.id, d.marked) for d in band3.items] == [(1, True), (2, True)]  # reset replaced the old content
    c1.flush(), c2.flush(), c3.flush()


def test_start_fresh_keeps_the_band_and_saves_it(tmp_path):
    features.set_mode(features.ADVANCED)
    root, _ = card(tmp_path)
    store = SessionStore(tmp_path / "s")
    band = Band()
    band.add(0, CROP, crop=RECT)
    cat = open_card(root)
    c = ResumeController(cat, root, store, interval_ms=60000, band=band)
    cat.set_mark(0, True)
    c.flush()
    c.start_fresh()
    assert len(band) == 1
    c.tick()
    assert c.wait_idle()
    assert len(store.load(card_key(root, cat.pictures), root, cat.pictures).derived) == 1


# ---- copy sheet -------------------------------------------------------------------------------

def run_sheet(sheet):
    done = []
    sheet.finished.connect(done.append)
    sheet.copy_button.click()
    assert wait_for(lambda: done)
    sheet.wait_idle()
    QApplication.processEvents()
    return done[0]


def ed_files(tmp_path):
    return sorted(f.name for f in (tmp_path / "out" / "ed").iterdir())


def test_sheet_with_derived_pairs_only_writes_ed_files_and_counts_them(tmp_path):
    src, pics = card(tmp_path)
    a, b = pics[0], pics[1]
    pairs = [(a, Derived(1, 0, CROP, crop=RECT, ratio=Ratio(4))), (a, Derived(2, 0, STORY, story=Story())),
             (b, Derived(3, 1, CROP, crop=RECT))]
    sheet = CopySheet([], tmp_path / "out", derived=pairs)
    assert sheet.copy_button.isEnabled() and "2 cropped" in sheet.plan_label.text()
    assert "1 story fit" in sheet.plan_label.text()
    res = run_sheet(sheet)
    assert ed_files(tmp_path) == ["A_4x5.jpg", "A_story.jpg", "B.jpg"]
    assert not [f for sub in ("raw", "soc") for f in (tmp_path / "out" / sub).rglob("*") if f.is_file()]
    assert len(res.cropped) == 2 and len(res.stories) == 1 and not res.failed
    text = sheet.result_label.text()
    assert "cropped 2 into ed" in text and "story 1 into ed" in text


def test_original_not_selected_but_derived_exports(tmp_path):
    src, pics = card(tmp_path)
    sheet = CopySheet([pics[2]], tmp_path / "out", derived=[(pics[0], Derived(1, 0, CROP, crop=RECT))])
    run_sheet(sheet)
    assert ed_files(tmp_path) == ["A.jpg"]
    assert [f.name for f in (tmp_path / "out" / "soc").iterdir()] == ["C.JPG"]


def test_two_crops_of_one_source_same_ratio_get_the_n_name(tmp_path):
    src, pics = card(tmp_path)
    a = pics[0]
    pairs = [(a, Derived(1, 0, CROP, crop=RECT, ratio=Ratio(4))),
             (a, Derived(2, 0, CROP, crop=(0.2, 0.2, 0.8, 0.8), ratio=Ratio(4)))]
    sheet = CopySheet([], tmp_path / "out", derived=pairs)
    res = run_sheet(sheet)
    assert ed_files(tmp_path) == ["A_4x5-1.jpg", "A_4x5.jpg"]
    assert len(res.cropped) == 2 and "cropped 2 into ed" in sheet.result_label.text()


def test_turned_source_swaps_the_suffix(tmp_path):
    src, pics = card(tmp_path)
    a = pics[0]
    d = Derived(1, 0, CROP, crop=RECT, ratio=Ratio(4))
    sheet = CopySheet([], tmp_path / "out", derived=[(a, d)], rotations={a.primary: 1})
    assert [i.dst.name for i in sheet.plan.crop_items] == ["A" + export_tail(d, 1)] == ["A_5x4.jpg"]
    assert sheet.plan.crop_items[0].crop_job.turns == 1
    run_sheet(sheet)
    assert ed_files(tmp_path) == ["A_5x4.jpg"]
    with Image.open(tmp_path / "out" / "ed" / "A_5x4.jpg") as im:
        assert im.size == (200, 300)  # the 300x200 crop with the source's one turn baked in


def test_story_derived_uses_the_sources_turns_and_legacy_kwargs_still_work(tmp_path):
    src, pics = card(tmp_path)
    a, b = pics[0], pics[1]
    sheet = CopySheet([b], tmp_path / "out", crops={b.primary: RECT}, rotations={a.primary: 3},
                      derived=[(a, Derived(1, 0, STORY, story=Story()))])
    jobs = sheet._story_jobs()
    assert [(j.primary, j.turns) for j in jobs] == [(a.primary, 3)]
    assert [i.dst.name for i in sheet.plan.crop_items] == ["B.jpg"]
    assert [i.dst.name for i in sheet.plan.story_items] == ["A_story.jpg"]


def test_garbage_derived_pairs_are_ignored(tmp_path):
    src, pics = card(tmp_path)
    sheet = CopySheet([pics[0]], tmp_path / "out",
                      derived=[(pics[0], "x"), None, (1, 2, 3), ("p", Derived(1, 0, CROP, crop=RECT)),
                               (pics[0], Derived(2, 0, CROP, crop=(0, 0, 'x', 1)))])
    assert sheet.plan.crop_items == [] and sheet.plan.story_items == []
    assert to_row(Derived(1, 0, CROP, crop=RECT))["kind"] == "crop"
