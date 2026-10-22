"""Pill model: swatches, PillBook, PillIndex."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from spuza import capture, catalog, pills
from spuza.pills import MAX_PILLS, PillBook, PillIndex, nearest_swatch, swatch

PILLS_JSON = Path("E:/work/MrAgent-wt/spuza-pills-build/state/research/spuza-pills/mockups/pills.json")


@pytest.fixture(autouse=True)
def pills_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPUZA_PILLS_DIR", str(tmp_path / "env-pills"))


def pic(tmp_path, name="IMG_0001.JPG", size=100, mtime=None, folder="card", size_bytes=None):
    p = tmp_path / folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    if mtime:
        os.utime(p, (mtime, mtime))
    return catalog.Picture(stem=p.stem, primary=p, sidecar=None, files=(p,), kind="JPG",
                           size_bytes=size if size_bytes is None else size_bytes, mtime=p.stat().st_mtime)


def test_swatches_match_expected():
    got = [(s.name, s.finder, s.dark, s.light) for s in pills.SWATCHES]
    assert got == [("None", 0, "#d9d9d9", "#3c3c3c"), ("Gray", 1, "#8e8e96", "#85858d"),
                   ("Green", 2, "#8fd14f", "#4f9a14"), ("Purple", 3, "#b266e6", "#8e44c7"),
                   ("Blue", 4, "#2fb4e8", "#1480b8"), ("Yellow", 5, "#f2d12e", "#cfa400"),
                   ("Red", 6, "#ff4d4d", "#d92d20"), ("Orange", 7, "#ff8a1f", "#e0690a")]


def test_swatches_match_pills_json():
    if not PILLS_JSON.exists():
        pytest.skip("research file not present")
    rows = json.loads(PILLS_JSON.read_text(encoding="utf-8"))
    assert [(r["name"], r["finder"], r["dark"], r["light"]) for r in rows] == \
        [(s.name, s.finder, s.dark, s.light) for s in pills.SWATCHES]


def test_swatch_lookup_and_nearest():
    assert swatch("RED").finder == 6
    assert swatch("nope").name == "None"
    assert nearest_swatch("#ff4c4c").name == "Red"
    assert nearest_swatch("#d92d20").name == "Red"  # a light value maps too
    assert nearest_swatch("#2fb4e9").name == "Blue"
    assert nearest_swatch("garbage").name == "None"


def test_create_digits_and_reuse():
    b = PillBook()
    a = b.create("  Keep ", "#ff0000")
    assert (a.name, a.digit) == ("Keep", 1)
    assert b.create("Print", "#00ff00").digit == 2
    assert b.create("Web", "#0000ff").digit == 3
    b.delete("print")
    assert b.create("Edit", "#123456").digit == 2  # lowest free digit
    assert [p.digit for p in b.pills()] == [1, 2, 3]
    assert b.by_digit(3).name == "Web" and b.by_digit(9) is None
    assert b.by_name("KEEP").digit == 1


def test_create_refusals():
    b = PillBook()
    b.create("A", "#ff0000")
    for bad in ("", "   ", "a", "x\ny"):
        with pytest.raises(ValueError):
            b.create(bad, "#ff0000")
    for i in range(MAX_PILLS - 1):
        b.create(f"P{i}", "#ff0000")
    with pytest.raises(ValueError):
        b.create("Tenth", "#ff0000")
    assert len(b.pills()) == 9


def test_rename_recolor_delete():
    b = PillBook()
    b.create("A", "#ff0000")
    b.create("B", "#00ff00")
    b.rename("a", "Alpha")
    assert b.by_name("alpha").digit == 1
    with pytest.raises(ValueError):
        b.rename("Alpha", "b")
    with pytest.raises(ValueError):
        b.rename("Alpha", " ")
    with pytest.raises(ValueError):
        b.rename("missing", "Z")
    b.rename("Alpha", "ALPHA")  # case-only change of itself is allowed
    assert b.by_digit(1).name == "ALPHA"
    b.recolor("b", "#010203")
    assert b.by_name("B").color == "None"  # near-black hex: nearest is the None swatch (light #3c3c3c)
    with pytest.raises(ValueError):
        b.recolor("zzz", "#000000")
    b.delete("nothing")  # silent
    b.delete("B")
    assert b.by_name("B") is None


def test_adopt():
    b = PillBook()
    b.create("Keep", "#ff0000")
    tags = [SimpleNamespace(name="keep", hex="#00ff00"), SimpleNamespace(name="New", hex="#ff4c4c"),
            SimpleNamespace(name="New", hex="#ff4c4c"), SimpleNamespace(name="  ", hex="#000000")]
    added = b.adopt(tags)
    assert [p.name for p in added] == ["New"]
    assert added[0].color == "Red" and added[0].digit == 2


def test_adopt_with_full_book():
    b = PillBook()
    for i in range(8):
        b.create(f"P{i}", "#ff0000")
    added = b.adopt([SimpleNamespace(name="A", hex="#ff0000"), SimpleNamespace(name="B", hex="#ff0000")])
    assert [p.name for p in added] == ["A"]
    assert b.adopt([SimpleNamespace(name="C", hex="#ff0000")]) == []
    assert len(b.pills()) == 9


def test_tag_builds_pilltag():
    xmp = pytest.importorskip("spuza.xmp")
    b = PillBook()
    b.create("Keep", "#ff0000")
    t = b.tag("keep")
    assert isinstance(t, xmp.PillTag) and t.name == "Keep" and t.hex == swatch("Red").dark


def test_pills_dir_env_and_default(tmp_path, monkeypatch):
    assert pills.pills_dir() == tmp_path / "env-pills"
    monkeypatch.delenv("SPUZA_PILLS_DIR")
    assert pills.pills_dir().name == "pills"


def test_book_roundtrip_and_corrupt(tmp_path):
    b = PillBook()
    b.create("Keep", "#ff0000")
    b.create("Web", "#00ff00")
    assert b.save() is True
    assert not list((tmp_path / "env-pills").glob("*.tmp"))
    assert PillBook.load().pills() == b.pills()
    f = tmp_path / "env-pills" / "pills.json"
    junk_ok = b'{"pills": [{"name": 1}, 7, {"name":"ok","color":"#fff","digit":99}]}'
    for junk in (b"{not json", b"[]", b'{"pills": 5}', junk_ok, b"\xff\xfe", b""):
        f.write_bytes(junk)
        assert PillBook.load().pills() == []
    f.unlink()
    assert PillBook.load().pills() == []
    assert PillBook.load(tmp_path / "nowhere" / "x.json").pills() == []


def test_book_deeply_nested_json(tmp_path):
    f = tmp_path / "deep.json"
    f.write_text("[" * 20000 + "]" * 20000)
    assert PillBook.load(f).pills() == []


def test_save_failure_returns_false(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    b = PillBook(blocker / "sub" / "pills.json")
    b.create("A", "#ff0000")
    assert b.save() is False


def test_index_key_stable_and_size_sensitive(tmp_path):
    a = pic(tmp_path, size=100)
    a2 = catalog.Picture(**{**a.__dict__, "size_bytes": 999})  # companions changed size_bytes only
    idx = PillIndex()
    assert idx.key(a) == idx.key(a2)
    b = pic(tmp_path, name="IMG_0002.JPG", size=101)
    assert idx.key(a) != idx.key(b)
    c = pic(tmp_path, name="IMG_0001.JPG", size=150, folder="other")
    assert PillIndex().key(c) != PillIndex().key(a)
    assert "img_0001.jpg" in PillIndex().key(a)


def test_index_key_uses_capture_key_and_caches(tmp_path, monkeypatch):
    calls = []

    def fake(path):
        calls.append(path)
        return capture.CaptureKey("2026-01-02T03:04:05", "5", 12)

    monkeypatch.setattr(capture, "capture_key", fake)
    p = pic(tmp_path)
    idx = PillIndex()
    k1 = idx.key(p)
    assert idx.key(p) == k1 and len(calls) == 1
    assert "2026-01-02T03:04:05" in k1 and "img_0001.jpg" in k1 and k1.endswith("100")
    other = pic(tmp_path, folder="elsewhere")
    assert PillIndex().key(other) == k1  # same file content elsewhere, new index, new Picture


def test_index_key_fallback_uses_mtime(tmp_path):
    p1 = pic(tmp_path, mtime=1_700_000_000)
    k1 = PillIndex().key(p1)
    p2 = pic(tmp_path, mtime=1_700_000_100)
    assert PillIndex().key(p2) != k1


def test_index_key_unreadable_file(tmp_path):
    ghost = catalog.Picture(stem="g", primary=tmp_path / "ghost.jpg", sidecar=None, files=(), kind="JPG",
                            size_bytes=0, mtime=0.0)
    idx = PillIndex()
    assert isinstance(idx.key(ghost), str)
    idx.set(ghost, ["A"])  # must not raise
    assert idx.get(ghost) == ("A",)


def test_index_toggle_set_clear(tmp_path):
    p = pic(tmp_path)
    idx = PillIndex()
    assert idx.get(p) == ()
    assert idx.toggle(p, "Keep") is True
    assert idx.toggle(p, "web") is True
    assert idx.get(p) == ("Keep", "web")
    assert idx.toggle(p, "KEEP") is False
    assert idx.get(p) == ("web",)
    idx.set(p, ["A", "a", "B", ""])
    assert idx.get(p) == ("A", "B")
    idx.clear(p)
    assert idx.get(p) == ()


def test_index_rename_and_drop(tmp_path):
    p, q = pic(tmp_path), pic(tmp_path, name="IMG_0002.JPG", size=101)
    idx = PillIndex()
    idx.set(p, ["A", "B"])
    idx.set(q, ["a"])
    idx.rename_pill("a", "Z")
    assert idx.get(p) == ("Z", "B") and idx.get(q) == ("Z",)
    idx.set(q, ["Z", "B"])
    idx.rename_pill("Z", "B")  # merging into an existing name leaves one
    assert idx.get(q) == ("B",)
    idx.drop_pill("b")
    assert idx.get(p) == () and idx.get(q) == ()


def test_index_roundtrip_and_corrupt(tmp_path):
    p = pic(tmp_path)
    idx = PillIndex()
    idx.set(p, ["A", "B"])
    assert idx.save() is True
    assert PillIndex.load().get(p) == ("A", "B")
    f = tmp_path / "env-pills" / "index.json"
    for junk in (b"{bad", b"[]", b'{"entries": 3}', b'{"entries": {"k": 5, "j": {"names": 4}}}', b""):
        f.write_bytes(junk)
        assert PillIndex.load().get(p) == ()
    assert PillIndex.load(tmp_path / "none" / "i.json").get(p) == ()


def test_index_bound_prunes_old_and_caps(tmp_path, monkeypatch):
    now = 2_000_000_000.0
    monkeypatch.setattr(pills, "_now", lambda: now)
    idx = PillIndex()
    old = now - (pills.PRUNE_DAYS + 1) * 86400
    idx._entries = {f"k{i}": {"names": ["A"], "at": now - i} for i in range(pills.MAX_ENTRIES + 50)}
    idx._entries["stale"] = {"names": ["A"], "at": old}
    assert idx.save() is True
    loaded = PillIndex.load()
    assert len(loaded._entries) == pills.MAX_ENTRIES
    assert "stale" not in loaded._entries
    assert "k0" in loaded._entries and f"k{pills.MAX_ENTRIES + 49}" not in loaded._entries


def test_index_load_rejects_non_finite_at(tmp_path):
    f = tmp_path / "env-pills" / "index.json"
    f.parent.mkdir()
    f.write_text('{"entries": {"a": {"names": ["X"], "at": Infinity}, "b": {"names": ["X"], "at": NaN}, '
                 '"c": {"names": ["X"], "at": 5}}}')
    assert sorted(PillIndex.load()._entries) == ["c"]


def test_index_save_while_another_thread_toggles(tmp_path):
    import threading

    p = [pic(tmp_path, name=f"IMG_{i:04d}.JPG", size=100 + i) for i in range(20)]
    idx = PillIndex()
    errors = []

    def toggler():
        try:
            for i in range(2000):
                idx.toggle(p[i % 20], f"N{i % 7}")
        except Exception as e:  # pragma: no cover - the failure being tested
            errors.append(e)

    t = threading.Thread(target=toggler)
    t.start()
    try:
        for _ in range(200):
            assert idx.save() in (True, False)
            if not t.is_alive():
                break
    finally:
        t.join(timeout=60)
    assert not t.is_alive() and errors == []


def test_color_is_canonical_swatch_name():
    b = PillBook()
    assert b.create("A", "orange").color == "Orange"
    assert b.create("B", "#ff4c4c").color == "Red"
    assert b.recolor("A", "BLUE").color == "Blue"
    assert b.recolor("A", "#8fd14e").color == "Green"
    for bad in ("", "mauve", "#12", 5, None):
        with pytest.raises(ValueError):
            b.create("C", bad)
        with pytest.raises(ValueError):
            b.recolor("A", bad)
    assert [p.name for p in b.pills()] == ["A", "B"]


def test_tag_uses_swatch_dark(monkeypatch):
    import sys
    import types

    class PillTag:
        def __init__(self, name, hex):
            self.name, self.hex = name, hex

    monkeypatch.setitem(sys.modules, "spuza.xmp", types.SimpleNamespace(PillTag=PillTag))
    b = PillBook()
    b.create("Warm", "Orange")
    assert b.tag("warm").hex == "#ff8a1f"


def test_load_normalises_colours(tmp_path):
    f = tmp_path / "env-pills" / "pills.json"
    f.parent.mkdir()
    f.write_text(json.dumps({"pills": [{"name": "A", "color": "red", "digit": 1},
                                       {"name": "B", "color": "#2fb4e8", "digit": 2},
                                       {"name": "C", "color": "mauve", "digit": 3}]}))
    assert [(p.name, p.color) for p in PillBook.load().pills()] == [("A", "Red"), ("B", "Blue")]
