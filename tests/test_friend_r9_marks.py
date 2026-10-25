"""Friend round 9: a derived picture (crop or story fit) owns its pills and stars. Digits, Ctrl+0..5, the star bar and
the menus act on the derived row when it is current and leave its source alone; a new derived starts bare; the marks
ride in the session and in the copy's ed file. Synthetic pictures only (offscreen)."""

from __future__ import annotations

import dataclasses
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from spuza import derived as dv
from spuza import xmp
from spuza.catalog import scan
from spuza.derived import CROP, STORY, Band, Derived
from spuza.session import SessionStore, card_key
from spuza.story import Story
from spuza.ui.copy_sheet import CopySheet
from spuza.xmp import PillTag
from test_friend_r3_window import CTRL, make_window, pump
from test_friend_r7_export import RECT, card, run_sheet

CROP1 = (0.1, 0.2, 0.6, 0.7)
NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture(autouse=True)
def no_advance(monkeypatch):
    """These tests check what a key stores on the picture in view; the advance after a pill or stars (0.16.0) is
    tested in test_friend_r9_keys.py."""
    from spuza.ui.window import MainWindow
    monkeypatch.setattr(MainWindow, "_advance_after_mark", lambda self, at=None: None)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_LOGS_DIR", str(tmp_path / "spuza-logs"))
    monkeypatch.setenv("SPUZA_SESSIONS_DIR", str(tmp_path / "env-sessions"))


@pytest.fixture
def w():
    win = make_window(marked=())
    orig = win.prefetcher.info

    def info(i):
        got = orig(i)
        return dataclasses.replace(got, picture_size=(6000, 4000)) if got is not None else None

    win.prefetcher.info = info
    win.pill_book.create("Hero", "Blue")
    win.pill_book.create("Spare", "Red")
    win._pills_edited()
    yield win
    win.close()


def key(win, k, mod=NONE):
    win.setFocus()
    QTest.keyClick(win, k, mod)


def crop_it(win, i=0):
    win.go_to(i)
    pump(lambda: win.prefetcher.cached(win.catalog.index) is not None and win.viewer.has_image)
    key(win, Qt.Key_X)
    win._crop_overlay.set_rect_frac(CROP1)
    key(win, Qt.Key_Return)
    return win.band.get(win._cur_id)


def source_marks(win, i):
    p = win.catalog.pictures[i]
    return win.pill_index.get(p), win.pill_index.stars(p)


# ---- model -------------------------------------------------------------------------------------------

def test_a_new_derived_starts_bare():
    d = Band().add(0, CROP, crop=RECT)
    assert d.pills == () and d.stars is None


def test_band_sets_pills_and_stars_and_bumps_the_version():
    b = Band()
    d = b.add(0, CROP, crop=RECT)
    v = b.version
    assert b.set_pills(d.id, ["Hero", "Spare"]) and b.get(d.id).pills == ("Hero", "Spare") and b.version > v
    assert b.set_stars(d.id, 4) and b.get(d.id).stars == 4
    assert not b.set_pills(99, ["x"]) and not b.set_stars(99, 1)
    assert not b.set_stars(d.id, 9) and b.get(d.id).stars == 4  # out of range is refused
    with pytest.raises(Exception):
        d.pills = ("x",)  # frozen


def test_band_rename_and_drop_pill_are_case_insensitive():
    b = Band()
    x, y = b.add(0, CROP, crop=RECT), b.add(1, STORY, story=Story())
    b.set_pills(x.id, ["Hero", "Spare"])
    b.set_pills(y.id, ["Spare"])
    b.rename_pill("hero", "Star")
    assert b.get(x.id).pills == ("Star", "Spare")
    b.drop_pill("SPARE")
    assert b.get(x.id).pills == ("Star",) and b.get(y.id).pills == ()


def test_row_round_trip_and_defensive_decode():
    d = Derived(3, 1, CROP, crop=RECT, pills=("Hero",), stars=2)
    assert dv.from_row(dv.to_row(d), 1) == d
    bare = dv.to_row(dataclasses.replace(d, pills=(), stars=None))
    assert "pills" not in bare and "stars" not in bare
    row = dv.to_row(d)
    for bad in (7, "Hero", [1, None], {"a": 1}, [["x"]] * 3):
        got = dv.from_row({**row, "pills": bad}, 1)
        assert got is not None and all(isinstance(n, str) for n in got.pills)
    for bad in ("3", 9, -1, True, 2.5, [], float("nan"), None):
        assert dv.from_row({**row, "stars": bad}, 1).stars is None
    deep = []
    for _ in range(5000):
        deep = [deep]
    got = dv.from_row({**row, "pills": deep, "stars": deep}, 1)
    assert got is None or (got.pills == () and got.stars is None)


def test_session_round_trip_and_an_old_row_has_no_marks(tmp_path):
    src, pics = card(tmp_path)
    store = SessionStore(tmp_path / "s")
    k = card_key(src, pics)
    items = [Derived(1, 0, CROP, crop=RECT, pills=("Hero", "Spare"), stars=5), Derived(2, 1, STORY, story=Story())]
    store.save(k, src, pics, set(), 0, derived=items)
    assert store.load(k, src, pics).derived == tuple(items)
    path = tmp_path / "s" / f"{k}.json"
    doc = json.loads(path.read_text())
    assert doc["version"] == 2
    for r in doc["derived"]:
        r.pop("pills", None)
        r.pop("stars", None)
    path.write_text(json.dumps(doc))
    got = store.load(k, src, pics).derived
    assert [(d.pills, d.stars) for d in got] == [((), None), ((), None)]


# ---- window: keys act on the derived row --------------------------------------------------------------

def test_digit_on_a_derived_tags_the_derived_not_the_source(w):
    d = crop_it(w)
    assert source_marks(w, 0) == ((), None) and d.pills == ()
    key(w, Qt.Key_1)
    assert w.band.get(d.id).pills == ("Hero",)
    assert source_marks(w, 0) == ((), None)  # bug (b): the original carries no tag
    assert w._current_pill_names() == ("Hero",)
    assert [n for n, _c in w.inspector.pills] == ["Hero"]
    assert w.filmstrip.derived_pill_colors(w.band.get(d.id)) == [w.pill_book.by_name("Hero").color]
    assert w.filmstrip.pill_colors(0) == []  # the original's dots stay empty
    key(w, Qt.Key_1)  # pressing a held pill again removes it
    assert w.band.get(d.id).pills == ()
    key(w, Qt.Key_1)
    key(w, Qt.Key_2)
    assert w.band.get(d.id).pills == ("Hero", "Spare")
    key(w, Qt.Key_0)  # 0 clears the derived's pills
    assert w.band.get(d.id).pills == ()


def test_stars_on_a_derived_rate_the_derived_not_the_source(w):
    d = crop_it(w)
    key(w, Qt.Key_3, CTRL)
    assert w.band.get(d.id).stars == 3
    assert source_marks(w, 0) == ((), None)  # bug (a): ranking the crop does not rank the original
    assert w.inspector.stars == 3 and w.star_bar.stars() == 3 and w.pill.stars_text() == "★★★"
    assert w.filmstrip.derived_star_count(w.band.get(d.id)) == 3 and w.filmstrip.star_count(0) == 0
    w.star_bar.rated.emit(5)
    assert w.band.get(d.id).stars == 5 and source_marks(w, 0)[1] is None
    key(w, Qt.Key_0, CTRL)
    assert w.band.get(d.id).stars == 0


def test_the_original_keeps_its_own_marks_and_the_views_follow_the_selection(w):
    w.go_to(0)
    key(w, Qt.Key_1)
    key(w, Qt.Key_4, CTRL)
    d = crop_it(w)
    assert d.pills == () and d.stars is None
    assert w._current_pill_names() == () and w.star_bar.stars() == 0  # nothing inherited from the source
    key(w, Qt.Key_2)
    w.go_to(0)
    assert w._current_pill_names() == ("Hero",) and w.star_bar.stars() == 4
    assert source_marks(w, 0) == (("Hero",), 4)


def test_cropping_then_tagging_does_not_touch_the_next_picture(w):
    """Bug (c): X, Enter, then a digit. The tag lands on the derived only; no other picture is touched."""
    d = crop_it(w)
    key(w, Qt.Key_1)
    assert w.catalog.index == 0
    for i in range(len(w.catalog)):
        assert source_marks(w, i) == ((), None)
    assert w.band.get(d.id).pills == ("Hero",)


def test_menu_handlers_act_on_the_derived(w):
    d = crop_it(w)
    w._stars_key(2)
    w._pill_key(2)
    assert (w.band.get(d.id).stars, w.band.get(d.id).pills) == (2, ("Spare",))
    assert source_marks(w, 0) == ((), None)


def test_renaming_and_deleting_a_pill_follow_into_derived(w):
    d = crop_it(w)
    key(w, Qt.Key_1)
    key(w, Qt.Key_2)
    w.pill_book.rename("Hero", "Star")  # what the pill manager does: book, index, then the window is told
    w.pill_index.rename_pill("Hero", "Star")
    w._pills_edited()
    assert w.band.get(d.id).pills == ("Star", "Spare")
    w.pill_book.delete("Spare")
    w.pill_index.drop_pill("Spare")
    w._pills_edited()
    assert w.band.get(d.id).pills == ("Star",)


# ---- copy ----------------------------------------------------------------------------------------------

def test_a_derived_ed_file_carries_its_own_marks_and_the_original_its_own(tmp_path):
    src, pics = card(tmp_path)
    a = pics[0]
    hero, other = PillTag("Hero", "#2fb4e8"), PillTag("Other", "#e84f2f")
    d = Derived(1, 0, CROP, crop=RECT, pills=("Other",), stars=2)
    sheet = CopySheet([a], tmp_path / "out", organise=False, derived=[(a, d)], derived_pills={1: (other,)},
                      pills={a.primary: (hero,)}, ratings={a.primary: 4})
    run_sheet(sheet)
    (ed,) = scan(tmp_path / "out" / "ed")
    pills, stars = xmp.read_picture_meta(ed)
    assert [t.name for t in pills] == ["Other"] and stars == 2
    (orig,) = [p for p in scan(tmp_path / "out") if p.stem == "A" and p.primary.parent.name != "ed"]
    pills, stars = xmp.read_picture_meta(orig)
    assert [t.name for t in pills] == ["Hero"] and stars == 4


def test_an_unmarked_source_with_a_rated_derived_still_exports_the_derived_rating(tmp_path):
    src, pics = card(tmp_path)
    a = pics[0]
    sheet = CopySheet([], tmp_path / "out", derived=[(a, Derived(1, 0, CROP, crop=RECT, stars=5))])
    assert sheet.plan.crop_items[0].rating == 5 and sheet.plan.crop_items[0].pills == ()
    bare = CopySheet([], tmp_path / "out2", derived=[(a, Derived(2, 0, STORY, story=Story()))])
    assert bare.plan.story_items[0].rating is None  # a bare derived leaves the rating alone
    rated = CopySheet([a], tmp_path / "out3", ratings={a.primary: 3}, derived=[(a, Derived(3, 0, CROP, crop=RECT))])
    assert rated.plan.crop_items[0].rating is None and rated.plan.items[0].rating == 3  # not the source's


def test_the_window_hands_the_derived_marks_to_the_sheet(w):
    d = crop_it(w)
    key(w, Qt.Key_1)
    key(w, Qt.Key_4, CTRL)
    w.go_to(1)
    seen = {}

    class Sheet:
        def show(self):
            pass

    def factory(pictures, default_dest=None, parent=None, **kw):
        seen.update(kw)
        seen["pictures"] = list(pictures)
        return Sheet()

    w.copy_sheet_factory = factory
    w.open_copy()
    assert seen["pictures"] == []  # the source is unmarked
    assert [x.id for _p, x in seen["derived"]] == [d.id]
    assert [t.name for t in seen["derived_pills"][d.id]] == ["Hero"]
    assert not seen.get("ratings")  # the source has no rating of its own
    assert seen["derived"][0][1].stars == 4
