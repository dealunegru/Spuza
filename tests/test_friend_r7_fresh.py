"""Friend round 7: U (start fresh) also clears the marks of derived pictures, and its undo restores them."""
import pytest

from spuza import derived as dv
from spuza.derived import Band
from spuza.session import SessionStore, card_key
from spuza.story import Story

from spuza import features
from test_ui_resume import Clock, ctl, make_card, marks_of, open_card


@pytest.fixture(autouse=True)
def sessions_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_SESSIONS_DIR", str(tmp_path / "env-sessions"))


@pytest.fixture
def advanced():
    features.set_mode(features.ADVANCED)


def setup(tmp_path):
    root = make_card(tmp_path)
    store = SessionStore(tmp_path / "s")
    clock = Clock()
    cat = open_card(root)
    band = Band()
    c = ctl(cat, root, store, clock=clock, band=band)
    return root, store, clock, cat, band, c


def marked_ids(band):
    return {d.id for d in band.items if d.marked}


def test_start_fresh_unmarks_originals_and_derived(tmp_path, advanced):
    root, store, clock, cat, band, c = setup(tmp_path)
    cat.set_mark(1, True)
    a = band.add(2, dv.CROP, crop=(0.0, 0.0, 0.5, 0.5))
    b = band.add(3, dv.STORY, story=Story(1.0, 0.0, 0.0))
    text = c.start_fresh()
    assert marks_of(cat) == set() and marked_ids(band) == set()
    assert {d.id for d in band.items} == {a.id, b.id}  # not removed
    assert "3 marks" in text and "undo" in text


def test_start_fresh_with_only_derived_marked_is_not_nothing(tmp_path, advanced):
    root, store, clock, cat, band, c = setup(tmp_path)
    band.add(2, dv.CROP, crop=(0.0, 0.0, 0.5, 0.5))
    text = c.start_fresh()
    assert marked_ids(band) == set()
    assert "nothing" not in text and "1 mark " in text
    assert c.start_fresh() == "Marks back (1)"
    assert len(marked_ids(band)) == 1


def test_undo_within_ten_seconds_remarks_both(tmp_path, advanced):
    root, store, clock, cat, band, c = setup(tmp_path)
    cat.set_mark(1, True)
    a = band.add(2, dv.CROP, crop=(0.0, 0.0, 0.5, 0.5))
    band.add(3, dv.CROP, crop=(0.0, 0.0, 0.5, 0.5), marked=False)  # never marked: stays unmarked
    c.start_fresh()
    clock.t += 9.9
    assert c.start_fresh() == "Marks back (2)"
    assert marks_of(cat) == {1} and marked_ids(band) == {a.id}
    key = card_key(root, cat.pictures)
    assert c.wait_idle()
    r = store.load(key, root, cat.pictures)
    assert r.marks == frozenset({1}) and {d.id for d in r.derived if d.marked} == {a.id}


def test_undo_after_ten_seconds_is_a_new_fresh_start(tmp_path, advanced):
    root, store, clock, cat, band, c = setup(tmp_path)
    band.add(2, dv.CROP, crop=(0.0, 0.0, 0.5, 0.5))
    c.start_fresh()
    clock.t += 10.1
    assert "Marks back" not in c.start_fresh()
    assert marked_ids(band) == set()


def test_undo_skips_a_derived_picture_removed_meanwhile(tmp_path, advanced):
    root, store, clock, cat, band, c = setup(tmp_path)
    a = band.add(2, dv.CROP, crop=(0.0, 0.0, 0.5, 0.5))
    b = band.add(3, dv.CROP, crop=(0.0, 0.0, 0.5, 0.5))
    c.start_fresh()
    band.remove(a.id)
    clock.t += 1
    c.start_fresh()
    assert [d.id for d in band.items] == [b.id] and marked_ids(band) == {b.id}


def test_saved_session_after_u_and_after_undo_has_the_band_marks(tmp_path, advanced):
    root, store, clock, cat, band, c = setup(tmp_path)
    cat.set_mark(1, True)
    a = band.add(2, dv.CROP, crop=(0.0, 0.0, 0.5, 0.5))
    key = card_key(root, cat.pictures)
    c.flush()
    assert c.wait_idle()
    c.start_fresh()
    c.tick()
    assert c.wait_idle()
    r = store.load(key, root, cat.pictures)
    assert r.marks == frozenset() and [(d.id, d.marked) for d in r.derived] == [(a.id, False)]
    clock.t += 1
    c.start_fresh()
    assert c.wait_idle()
    r = store.load(key, root, cat.pictures)
    assert [(d.id, d.marked) for d in r.derived] == [(a.id, True)]


def test_without_a_band_nothing_changes(tmp_path, advanced):
    root = make_card(tmp_path)
    cat = open_card(root)
    c = ctl(cat, root, SessionStore(tmp_path / "s"), clock=Clock())
    cat.set_mark(1, True)
    assert "1 mark " in c.start_fresh()
    assert c.start_fresh() == "Marks back (1)"
