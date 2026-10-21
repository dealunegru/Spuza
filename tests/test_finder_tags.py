"""Finder tags mirror: payload, parse, nearest colour, and the darwin xattr path through a fake libc layer."""

import plistlib
from types import SimpleNamespace

from spuza import finder_tags as ft
from spuza import pills

ATTR = "com.apple.metadata:_kMDItemUserTags"
INFO = "com.apple.FinderInfo"


class FakeXattr:
    def __init__(self, store=None, fail=False):
        self.store = store if store is not None else {}
        self.fail = fail
        self.sets = []

    def get(self, path, name):
        if self.fail:
            raise OSError("boom")
        return self.store.get((str(path), name))

    def set(self, path, name, value):
        if self.fail:
            raise OSError("boom")
        self.sets.append(name)
        self.store[(str(path), name)] = bytes(value)


def tag(name, hexv):
    return SimpleNamespace(name=name, hex=hexv)


def darwin(monkeypatch, fake):
    monkeypatch.setattr(ft, "_xattr", fake)
    monkeypatch.setattr(ft.sys, "platform", "darwin")


def test_colour_names():
    assert ft.FINDER_COLOURS == ("none", "gray", "green", "purple", "blue", "yellow", "red", "orange")


def test_nearest_finder_each_swatch():
    for s in pills.SWATCHES:
        assert ft.nearest_finder(s.dark) == s.finder
        assert ft.nearest_finder(s.light) == s.finder
    assert ft.nearest_finder("#fe4d4d") == 6


def test_payload_roundtrip():
    data = ft.payload([("Keep", 6), ("Plain", 0)])
    assert data.startswith(b"bplist00")
    assert plistlib.loads(data) == ["Keep\n6", "Plain\n0"]
    assert ft.parse(data) == [("Keep", 6), ("Plain", 0)]


def test_parse_tolerant():
    data = plistlib.dumps(["Bare", "X\n9", "Y\nq", "Z\n3", 5], fmt=plistlib.FMT_BINARY)
    assert ft.parse(data) == [("Bare", 0), ("X", 0), ("Y", 0), ("Z", 3)]
    for junk in (b"", b"junk", plistlib.dumps({"a": 1}, fmt=plistlib.FMT_BINARY), b"bplist00" + b"\x00" * 5):
        assert ft.parse(junk) == []


def test_write_tags_merges_and_adds_finderinfo_once(tmp_path, monkeypatch):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    fake = FakeXattr({(str(f), ATTR): ft.payload([("Foreign", 3), ("Keep", 1)])})
    darwin(monkeypatch, fake)
    assert ft.write_tags(f, [tag("keep", "#ff4d4d"), tag("Web", "#2fb4e8")]) is True
    assert ft.parse(fake.store[(str(f), ATTR)]) == [("Foreign", 3), ("keep", 6), ("Web", 4)]
    assert fake.store[(str(f), INFO)] == bytes(32)
    assert ft.write_tags(f, [tag("Web", "#2fb4e8")]) is True
    assert fake.sets.count(INFO) == 1


def test_write_tags_keeps_existing_finderinfo(tmp_path, monkeypatch):
    f = tmp_path / "a.jpg"
    fake = FakeXattr({(str(f), INFO): b"\x01" * 32})
    darwin(monkeypatch, fake)
    assert ft.write_tags(f, [tag("A", "#8e8e96")]) is True
    assert fake.store[(str(f), INFO)] == b"\x01" * 32


def test_write_tags_corrupt_existing_attr_is_replaced(tmp_path, monkeypatch):
    f = tmp_path / "a.jpg"
    fake = FakeXattr({(str(f), ATTR): b"garbage"})
    darwin(monkeypatch, fake)
    assert ft.write_tags(f, [tag("A", "#8e8e96")]) is True
    assert ft.parse(fake.store[(str(f), ATTR)]) == [("A", 1)]


def test_write_tags_false_off_darwin(tmp_path, monkeypatch):
    fake = FakeXattr()
    monkeypatch.setattr(ft, "_xattr", fake)
    monkeypatch.setattr(ft.sys, "platform", "win32")
    assert ft.write_tags(tmp_path / "a.jpg", [tag("A", "#ff0000")]) is False
    assert fake.sets == []


def test_write_tags_never_raises(tmp_path, monkeypatch):
    darwin(monkeypatch, FakeXattr(fail=True))
    assert ft.write_tags(tmp_path / "a.jpg", [tag("A", "#ff0000")]) is False
    assert ft.write_tags(tmp_path / "a.jpg", [object()]) is False
    assert ft.write_tags(tmp_path / "a.jpg", None) is False


def test_write_tags_empty_is_noop_true(tmp_path, monkeypatch):
    fake = FakeXattr()
    darwin(monkeypatch, fake)
    assert ft.write_tags(tmp_path / "a.jpg", []) is True
    assert fake.sets == []


def test_write_tags_accepts_real_pills(tmp_path, monkeypatch):
    f = tmp_path / "a.jpg"
    fake = FakeXattr()
    darwin(monkeypatch, fake)
    book = pills.PillBook()
    book.create("Hexed", "#ff4d4d")
    book.create("Named", "Blue")
    book.create("Plain", "none")
    assert ft.write_tags(f, book.pills()) is True
    assert ft.parse(fake.store[(str(f), ATTR)]) == [("Hexed", 6), ("Named", 4), ("Plain", 0)]
