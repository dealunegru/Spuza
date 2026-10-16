"""Session store: key, atomic save, matched load, prune, and the card is never touched."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from spuza import catalog, session
from spuza.session import SessionStore, card_key


@pytest.fixture(autouse=True)
def sessions_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_SESSIONS_DIR", str(tmp_path / "env-sessions"))


def make_card(base: Path, n: int = 6, name: str = "card") -> Path:
    root = base / name
    (root / "DCIM").mkdir(parents=True)
    for i in range(n):
        (root / "DCIM" / f"IMG_{i:04d}.JPG").write_bytes(b"x" * (100 + i))
    return root


def scan(root):
    return catalog.scan(root)


def tree_state(root: Path):
    out = []
    for p in sorted(root.rglob("*")):
        st = p.stat()
        h = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""
        out.append((p.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns, h))
    return out


def test_default_dir_honours_env(tmp_path):
    assert session.default_dir() == tmp_path / "env-sessions"
    assert SessionStore().folder == tmp_path / "env-sessions"


def test_default_dir_without_env_is_sessions_under_appdata(monkeypatch):
    monkeypatch.delenv("SPUZA_SESSIONS_DIR")
    assert session.default_dir().name == "sessions"


def test_card_key_depends_on_names_and_sizes(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    k = card_key(root, pics)
    assert k == card_key(root, list(reversed(pics)))
    assert len(k) == 64 and int(k, 16) >= 0
    other = make_card(tmp_path, 6, "other")
    assert card_key(other, scan(other)) == k  # same list under another mount point
    (other / "DCIM" / "IMG_0002.JPG").write_bytes(b"y" * 7)
    assert card_key(other, scan(other)) != k
    (other / "DCIM" / "IMG_0009.JPG").write_bytes(b"z")
    assert card_key(other, scan(other)) != k


def test_round_trip_marks_and_index(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    k = card_key(root, pics)
    assert st.load(k, root, pics) is None
    assert not (tmp_path / "s").exists()  # reading never creates the folder
    st.save(k, root, pics, {1, 3}, 4, now=1000.0)
    r = st.load(k, root, pics)
    assert r.marks == frozenset({1, 3}) and r.index == 4 and r.saved_at == 1000.0 and r.dropped == 0
    assert [p.name for p in (tmp_path / "s").iterdir()] == [k + ".json"]


def test_changed_file_drops_its_mark(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    k = card_key(root, pics)
    st.save(k, root, pics, {1, 3}, 0)
    f = pics[1].primary
    os.utime(f, (pics[1].mtime + 3600, pics[1].mtime + 3600))  # re-dated, size unchanged: key still the same
    after = scan(root)
    r = st.load(k, root, after)
    assert [after[i].stem for i in r.marks] == [pics[3].stem] and r.dropped == 1


def test_mtime_tolerance_is_two_seconds(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    k = card_key(root, pics)
    st.save(k, root, pics, {2}, 0)
    os.utime(pics[2].primary, (pics[2].mtime + 1.5, pics[2].mtime + 1.5))
    after = scan(root)
    assert [after[i].stem for i in st.load(k, root, after).marks] == [pics[2].stem]


def test_other_card_and_reformatted_card_do_not_restore(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    st.save(card_key(root, pics), root, pics, {1}, 2)
    other = make_card(tmp_path, 4, "other")
    opics = scan(other)
    assert st.load(card_key(other, opics), other, opics) is None
    fmt = make_card(tmp_path, 6, "fmt")
    (fmt / "DCIM" / "IMG_0005.JPG").write_bytes(b"?" * 9)  # reformatted: different list
    fp = scan(fmt)
    assert st.load(card_key(fmt, fp), fmt, fp) is None


def test_place_by_name_before_number(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    k = card_key(root, pics)
    st.save(k, root, pics, {0}, 4)
    p = st.folder / (k + ".json")
    doc = json.loads(p.read_text())
    doc["index"] = 1  # number disagrees; the name wins
    p.write_text(json.dumps(doc))
    assert st.load(k, root, pics).index == 4
    doc["index_name"] = "gone.jpg"
    doc["index"] = 99
    p.write_text(json.dumps(doc))
    assert st.load(k, root, pics).index == len(pics) - 1  # clamped number


@pytest.mark.parametrize("raw", [
    b"", b"{", b"[]", b"null", b"\xff\xfe", b'{"version": 2, "marks": []}',
    b'{"version": 1, "saved_at": "x", "marks": []}', b'{"version": 1, "saved_at": NaN, "marks": []}',
    b'{"version": 1, "saved_at": 1, "marks": 5}',
    b"[" * 100000, b'{"a":' * 100000,
], ids=lambda r: r[:12].decode("latin-1"))
def test_malformed_file_is_none_not_a_crash(tmp_path, raw):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    k = card_key(root, pics)
    st.folder.mkdir()
    (st.folder / (k + ".json")).write_bytes(raw)
    assert st.load(k, root, pics) is None


def test_odd_mark_rows_are_dropped_not_fatal(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    k = card_key(root, pics)
    st.folder.mkdir()
    doc = {"version": 1, "saved_at": 5, "index": "x", "index_name": 7, "marks": [
        1, {"name": "DCIM/IMG_0000.JPG", "size": "100", "mtime": 0}, {"name": "DCIM/IMG_0001.JPG"},
        {"name": "DCIM/IMG_0002.JPG", "size": True, "mtime": pics[2].mtime}]}
    (st.folder / (k + ".json")).write_text(json.dumps(doc))
    r = st.load(k, root, pics)
    assert r is None  # all four rows dropped, no place to restore


def test_too_large_file_is_none(tmp_path, monkeypatch):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    k = card_key(root, pics)
    st.save(k, root, pics, {1}, 2)
    assert st.load(k, root, pics) is not None
    monkeypatch.setattr(session, "MAX_BYTES", 10)
    assert st.load(k, root, pics) is None


def test_bad_key_is_ignored(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    for bad in ("../x", "a/b", "", "a.b"):
        st.save(bad, root, pics, {1}, 0)
        assert st.load(bad, root, pics) is None
        st.forget(bad)
    assert not (tmp_path / "s").exists()


def test_crash_mid_write_leaves_the_old_file(tmp_path, monkeypatch):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    k = card_key(root, pics)
    st.save(k, root, pics, {1}, 2)
    before = (st.folder / (k + ".json")).read_bytes()

    def boom(*a, **kw):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    st.save(k, root, pics, {1, 2, 3}, 5)  # must not raise
    monkeypatch.undo()
    assert (st.folder / (k + ".json")).read_bytes() == before
    assert [p.name for p in st.folder.iterdir()] == [k + ".json"]  # temp file cleaned up
    assert st.load(k, root, pics).marks == frozenset({1})


def test_unwritable_folder_never_raises(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    SessionStore(blocker / "sub").save(card_key(root, pics), root, pics, {1}, 0)  # folder under a file


def test_forget(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    k = card_key(root, pics)
    st.forget(k)  # nothing there: fine
    st.save(k, root, pics, {1}, 2)
    st.forget(k)
    assert st.load(k, root, pics) is None


def test_prune_by_saved_at(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    st = SessionStore(tmp_path / "s")
    day = 86400.0
    st.save("aaa", root, pics, {1}, 0, now=1000.0)
    st.save("bbb", root, pics, {1}, 0, now=1000.0 + 20 * day)
    (st.folder / "ccc.json").write_text("junk")  # unreadable: aged by its mtime (now, so it stays)
    assert st.prune(30, now=1000.0 + 31 * day) == 1
    assert sorted(p.name for p in st.folder.iterdir()) == ["bbb.json", "ccc.json"]
    assert SessionStore(tmp_path / "nope").prune() == 0


def test_save_restore_leaves_the_card_untouched(tmp_path):
    root = make_card(tmp_path)
    store_dir = tmp_path / "store"
    before_card = tree_state(root)
    before_parent = sorted(p.name for p in tmp_path.iterdir())
    pics = scan(root)
    st = SessionStore(store_dir)
    k = card_key(root, pics)
    st.save(k, root, pics, {0, 2}, 3)
    assert st.load(k, root, pics).marks == frozenset({0, 2})
    st.forget(k)
    st.save(k, root, pics, {1}, 1)
    assert tree_state(root) == before_card
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(before_parent + ["store"])  # only the store appeared
    assert [p.name for p in store_dir.iterdir()] == [k + ".json"]


def test_session_module_imports_no_qt_at_import():
    assert "QStandardPaths" not in vars(session)


def test_relative_names_use_forward_slashes_and_respect_folder_boundaries(tmp_path):
    root = make_card(tmp_path)
    pics = scan(root)
    assert session._rel(root, pics[0]) == f"DCIM/{pics[0].primary.name}"
    assert session._rel(str(root) + os.sep, pics[0]) == f"DCIM/{pics[0].primary.name}"
    assert session._rel(str(root)[:-1], pics[0]) == pics[0].primary.name  # "card" vs "car": not a parent folder
