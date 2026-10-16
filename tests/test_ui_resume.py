"""ResumeController: quit-and-reopen, debounce, start fresh + undo, classic does nothing."""

import hashlib
import threading
from pathlib import Path

import pytest

from spuza import catalog, features
from spuza.session import SessionStore, card_key
from spuza.ui.resume import ResumeController


@pytest.fixture(autouse=True)
def sessions_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_SESSIONS_DIR", str(tmp_path / "env-sessions"))


@pytest.fixture
def advanced():
    features.set_mode(features.ADVANCED)


def make_card(base: Path, n: int = 8, name: str = "card") -> Path:
    root = base / name
    (root / "DCIM").mkdir(parents=True)
    for i in range(n):
        (root / "DCIM" / f"IMG_{i:04d}.JPG").write_bytes(b"x" * (100 + i))
    return root


def open_card(root):
    return catalog.Catalog(catalog.scan(root))


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def marks_of(cat):
    return {i for i in range(len(cat)) if cat.is_marked(i)}


def ctl(cat, root, store, **kw):
    kw.setdefault("interval_ms", 60000)  # tests drive tick() themselves
    return ResumeController(cat, root, store, **kw)


class SpyStore(SessionStore):
    def __init__(self, folder):
        super().__init__(folder)
        self.calls = []

    def save(self, *a, **kw):
        self.calls.append("save")
        super().save(*a, **kw)

    def load(self, *a, **kw):
        self.calls.append("load")
        return super().load(*a, **kw)

    def forget(self, *a, **kw):
        self.calls.append("forget")
        super().forget(*a, **kw)


def test_quit_and_reopen_restores_marks_and_place(tmp_path, advanced):
    root = make_card(tmp_path)
    store = SessionStore(tmp_path / "s")
    cat = open_card(root)
    c1 = ctl(cat, root, store)
    for i in (1, 4, 5):
        cat.set_mark(i, True)
    cat.go(6)
    c1.flush()
    assert c1.wait_idle()

    cat2 = open_card(root)
    c2 = ctl(cat2, root, store)
    banner = c2.restore()
    assert marks_of(cat2) == {1, 4, 5} and cat2.index == 6
    assert banner.startswith("Restored 3 marks and your place (picture 7) from ")
    assert banner.endswith(" · U to start fresh")
    c2.tick()
    assert c2.wait_idle()  # restore itself is not a change: nothing to write


def test_restore_without_marks_says_place_only(tmp_path, advanced):
    root = make_card(tmp_path)
    store = SessionStore(tmp_path / "s")
    cat = open_card(root)
    c = ctl(cat, root, store)
    cat.go(3)
    c.flush()
    cat2 = open_card(root)
    banner = ctl(cat2, root, store).restore()
    assert banner.startswith("Restored your place (picture 4) from ")


def test_nothing_saved_or_opened_alone_restores_nothing_and_writes_nothing(tmp_path, advanced):
    root = make_card(tmp_path)
    store = SpyStore(tmp_path / "s")
    c = ctl(open_card(root), root, store)
    assert c.restore() is None
    c.tick()
    c.flush()
    assert store.calls == ["load"]
    assert not (tmp_path / "s").exists()


def test_changed_file_is_dropped_and_mentioned(tmp_path, advanced):
    root = make_card(tmp_path)
    store = SessionStore(tmp_path / "s")
    cat = open_card(root)
    c = ctl(cat, root, store)
    cat.set_mark(1, True)
    cat.set_mark(2, True)
    c.flush()
    (root / "DCIM" / "IMG_0001.JPG").write_bytes(b"x" * 101)  # same size as before: only the mtime moves
    import os
    p = root / "DCIM" / "IMG_0001.JPG"
    os.utime(p, (p.stat().st_mtime + 7200,) * 2)
    cat2 = open_card(root)
    banner = ctl(cat2, root, store).restore()
    assert [cat2.pictures[i].stem for i in marks_of(cat2)] == ["IMG_0002"]  # the scan re-sorts by date
    assert "1 changed or missing skipped" in banner


def test_other_card_does_not_restore(tmp_path, advanced):
    a, b = make_card(tmp_path, 8, "a"), make_card(tmp_path, 5, "b")
    store = SessionStore(tmp_path / "s")
    cat = open_card(a)
    c = ctl(cat, a, store)
    cat.set_mark(1, True)
    c.flush()
    catb = open_card(b)
    assert ctl(catb, b, store).restore() is None
    assert marks_of(catb) == set()


def test_malformed_json_restores_nothing(tmp_path, advanced):
    root = make_card(tmp_path)
    store = SessionStore(tmp_path / "s")
    cat = open_card(root)
    store.folder.mkdir()
    (store.folder / (card_key(root, cat.pictures) + ".json")).write_text("{ nope")
    assert ctl(cat, root, store).restore() is None
    assert marks_of(cat) == set() and cat.index == 0


def test_classic_does_nothing(tmp_path):
    assert not features.enabled("resume_session")
    root = make_card(tmp_path)
    store = SpyStore(tmp_path / "s")
    cat = open_card(root)
    c = ctl(cat, root, store, interval_ms=1)
    cat.set_mark(2, True)
    cat.go(3)
    assert c.restore() is None
    c.tick()
    assert c.start_fresh() == ""
    c.flush()
    assert c.wait_idle()
    assert store.calls == []
    assert marks_of(cat) == {2}  # start_fresh did not clear anything either
    assert not (tmp_path / "s").exists()
    assert not (tmp_path / "env-sessions").exists()


def test_classic_default_store_is_never_built(tmp_path, monkeypatch):
    monkeypatch.setattr("spuza.ui.resume.SessionStore", lambda *a, **k: pytest.fail("store built in classic"))
    root = make_card(tmp_path)
    c = ResumeController(open_card(root), root)
    c.tick()
    c.restore()
    c.flush()


def test_toggling_the_flag_live_starts_saving(tmp_path):
    root = make_card(tmp_path)
    store = SpyStore(tmp_path / "s")
    cat = open_card(root)
    c = ctl(cat, root, store)
    cat.set_mark(1, True)
    c.tick()
    assert store.calls == []
    features.set_mode(features.ADVANCED)
    c.tick()
    assert c.wait_idle()
    assert store.calls == ["save"]


def test_set_enabled_false_stops_saving(tmp_path, advanced):
    root = make_card(tmp_path)
    store = SpyStore(tmp_path / "s")
    cat = open_card(root)
    c = ctl(cat, root, store)
    c.set_enabled(False)
    cat.set_mark(1, True)
    c.tick()
    c.flush()
    assert c.restore() is None
    assert store.calls == []
    c.set_enabled(True)
    c.tick()
    assert c.wait_idle()
    assert store.calls == ["save"]


def test_debounce_fifty_toggles_make_few_writes(tmp_path, advanced):
    root = make_card(tmp_path)
    store = SpyStore(tmp_path / "s")
    cat = open_card(root)
    c = ctl(cat, root, store)
    for n in range(50):
        cat.toggle_mark(n % 8)
        if n % 25 == 24:
            c.tick()  # two timer ticks in the burst
    c.tick()
    assert c.wait_idle()
    assert 1 <= store.calls.count("save") <= 3
    c.tick()
    assert c.wait_idle()
    n = store.calls.count("save")
    c.tick()
    assert store.calls.count("save") == n  # unchanged snapshot: no write


def test_one_write_in_flight_and_coalescing(tmp_path, advanced):
    root = make_card(tmp_path)
    gate, entered = threading.Event(), threading.Event()
    seen = []

    class Slow(SessionStore):
        def save(self, key, root_, pictures, marks, index, now=None):
            seen.append((frozenset(marks), index))
            entered.set()
            assert gate.wait(5)
            super().save(key, root_, pictures, marks, index, now)

    cat = open_card(root)
    c = ctl(cat, root, Slow(tmp_path / "s"))
    cat.set_mark(1, True)
    c.tick()
    assert entered.wait(5)
    for i in (2, 3, 4):  # three more changes while the first write is stuck
        cat.set_mark(i, True)
        c.tick()
    assert len(seen) == 1
    gate.set()
    assert c.wait_idle(5)
    assert seen == [(frozenset({1}), 0), (frozenset({1, 2, 3, 4}), 0)]  # the middle two were coalesced


def test_start_fresh_clears_forgets_and_undoes(tmp_path, advanced):
    root = make_card(tmp_path)
    store = SessionStore(tmp_path / "s")
    clock = Clock()
    cat = open_card(root)
    c = ctl(cat, root, store, clock=clock)
    for i in (1, 2, 3):
        cat.set_mark(i, True)
    cat.go(5)
    c.flush()
    assert c.wait_idle()
    key = card_key(root, cat.pictures)

    text = c.start_fresh()
    assert marks_of(cat) == set() and cat.index == 5
    assert "3 marks" in text and "undo" in text
    assert store.load(key, root, cat.pictures) is None
    c.tick()
    assert c.wait_idle()
    assert store.load(key, root, cat.pictures) is None  # clearing is not re-saved by the next tick

    clock.t += 9.9
    assert c.start_fresh() == "Marks back (3)"
    assert marks_of(cat) == {1, 2, 3}
    assert store.load(key, root, cat.pictures).marks == frozenset({1, 2, 3})  # re-saved


def test_start_fresh_does_not_block_behind_a_stuck_store_lock(tmp_path, advanced, monkeypatch):
    root = make_card(tmp_path)
    monkeypatch.setattr("spuza.ui.resume.IO_WAIT_SECONDS", 0.05)
    store = SpyStore(tmp_path / "s")
    cat = open_card(root)
    c = ctl(cat, root, store)
    for i in (1, 2):
        cat.set_mark(i, True)
    held = threading.Event()
    release = threading.Event()

    def hold():  # a stuck store operation owning the lock
        with c._io:
            held.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(5)
        text = c.start_fresh()  # returns after the bound, never hangs
        assert marks_of(cat) == set() and "busy" in text and "2 marks" in text
        assert "forget" not in store.calls  # the forget was skipped
    finally:
        release.set()
        t.join(5)
    assert not t.is_alive()
    c.tick()  # the cleared state is saved once the lock is free
    assert c.wait_idle(5)
    key = card_key(root, cat.pictures)
    assert store.load(key, root, cat.pictures) is None  # nothing marked and place 0: nothing worth restoring


def test_undo_does_not_block_behind_a_stuck_store_lock(tmp_path, advanced, monkeypatch):
    root = make_card(tmp_path)
    monkeypatch.setattr("spuza.ui.resume.IO_WAIT_SECONDS", 0.05)
    cat = open_card(root)
    clock = Clock()
    c = ctl(cat, root, SpyStore(tmp_path / "s"), clock=clock)
    cat.set_mark(2, True)
    c.start_fresh()
    held, release = threading.Event(), threading.Event()

    def hold():
        with c._io:
            held.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(5)
        assert c.start_fresh() == "Marks back (1)" and marks_of(cat) == {2}
    finally:
        release.set()
        t.join(5)
    assert c._last is None  # not yet saved: the next tick queues it
    c.tick()
    assert c.wait_idle(5)
    assert c._store.load(card_key(root, cat.pictures), root, cat.pictures).marks == frozenset({2})


def test_start_fresh_second_press_after_ten_seconds_is_not_an_undo(tmp_path, advanced):
    root = make_card(tmp_path)
    clock = Clock()
    cat = open_card(root)
    c = ctl(cat, root, SessionStore(tmp_path / "s"), clock=clock)
    cat.set_mark(1, True)
    c.start_fresh()
    clock.t += 10.5
    c.start_fresh()
    assert marks_of(cat) == set()


def test_start_fresh_with_nothing_marked(tmp_path, advanced):
    root = make_card(tmp_path)
    c = ctl(open_card(root), root, SessionStore(tmp_path / "s"))
    assert c.start_fresh() == "Started fresh · nothing was marked"


def test_undo_with_saving_off_does_not_write(tmp_path, advanced):
    root = make_card(tmp_path)
    store = SpyStore(tmp_path / "s")
    cat = open_card(root)
    c = ctl(cat, root, store, clock=Clock())
    c.set_enabled(False)
    cat.set_mark(1, True)
    c.start_fresh()
    c.start_fresh()
    assert marks_of(cat) == {1}
    assert "save" not in store.calls


def test_flush_writes_synchronously_and_is_bounded(tmp_path, advanced):
    root = make_card(tmp_path)
    store = SessionStore(tmp_path / "s")
    cat = open_card(root)
    c = ctl(cat, root, store)
    cat.set_mark(3, True)
    c.flush()
    assert store.load(card_key(root, cat.pictures), root, cat.pictures).marks == frozenset({3})


def test_flush_gives_up_on_a_stuck_write(tmp_path, advanced, monkeypatch):
    root = make_card(tmp_path)
    release = threading.Event()

    class Stuck(SessionStore):
        def save(self, *a, **kw):
            release.wait(10)

    monkeypatch.setattr("spuza.ui.resume.JOIN_SECONDS", 0.05)
    cat = open_card(root)
    c = ctl(cat, root, Stuck(tmp_path / "s"))
    cat.set_mark(1, True)
    c.flush()  # returns after the bound although the write is stuck
    assert not c.wait_idle(0.05)
    release.set()
    assert c.wait_idle(5)


def test_round_trip_leaves_the_card_byte_identical(tmp_path, advanced):
    root = make_card(tmp_path)

    def state():
        return [(p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns,
                 hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "")
                for p in sorted(root.rglob("*"))]

    before, siblings = state(), sorted(p.name for p in tmp_path.iterdir())
    store_dir = tmp_path / "store"
    cat = open_card(root)
    c = ctl(cat, root, SessionStore(store_dir), clock=Clock())
    cat.set_mark(2, True)
    cat.go(4)
    c.flush()
    ctl(open_card(root), root, SessionStore(store_dir)).restore()
    c.start_fresh()
    c.start_fresh()
    assert state() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(siblings + ["store"])
    assert all(p.suffix == ".json" for p in store_dir.iterdir())
