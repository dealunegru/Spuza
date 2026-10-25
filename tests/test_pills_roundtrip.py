"""Task spp-e2e: pills survive a real copy. Mark + pill -> copy -> a NEW window on the destination shows them again.

Offscreen QApplication; real MainWindow, real CopySheet, real run_copy on a synthetic card in tmp_path (< 1 MB).
Nothing is faked: the destination is set on the real sheet and the copy runs on its own worker thread.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from spuza import xmp
from spuza.catalog import Catalog, Prefetcher, scan
from spuza.ui.window import MainWindow

from test_pills_ui import pump, settle  # noqa: F401  (also makes the QApplication)
from test_ui_copy_sheet import wait_for  # noqa: E402

FOREIGN = (
    b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    b'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="CameraX">'
    b'<dc:subject><rdf:Bag><rdf:li>FromCamera</rdf:li></rdf:Bag></dc:subject>'
    b'</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
)
# stem -> the pills (digit keys: 1 = Hero, 2 = Delivered) each picture is given
WANTED = {"A": ("Hero",), "B": ("Hero", "Delivered"), "C": ("Delivered",), "D": ("Hero",)}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def make_card(card: Path) -> None:
    card.mkdir()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(card / "A.jpg")
    Image.new("RGB", (64, 48), (30, 200, 30)).save(card / "B.jpg", quality=80)
    Image.new("RGB", (64, 48), (30, 30, 200)).save(card / "C.png")
    Image.new("RGB", (64, 48), (120, 120, 30)).save(card / "D.JPG")
    (card / "D.RAF").write_bytes(bytes(range(256)) * 40)  # random-looking RAW bytes, never decoded
    (card / "D.xmp").write_bytes(FOREIGN)


def open_window(folder: Path) -> MainWindow:
    cat = Catalog(scan(folder))
    w = MainWindow(cat, Prefetcher(cat), folder)
    w.resize(1200, 800)
    w.show()
    pump(lambda: w.viewer.has_image)
    return w


def stem_index(w: MainWindow, stem: str) -> int:
    return next(i for i, p in enumerate(w.catalog.pictures) if p.stem == stem)


def pills_of(w: MainWindow, stem: str) -> set[str]:
    return set(w.pill_index.get(w.catalog.pictures[stem_index(w, stem)]))


def lit(w: MainWindow) -> list[str]:
    return [c.label for c in w.pill_tray.capsules() if c.kind == "pill" and c.lit]


@pytest.fixture
def journey(tmp_path):
    """Source window: two pills, digits pressed, every picture marked, one real copy into tmp_path/dest."""
    card, dest = tmp_path / "card", tmp_path / "dest"
    make_card(card)
    dest.mkdir()
    w = open_window(card)
    w.pill_book.create("Hero", "Blue")
    w.pill_book.create("Delivered", "Green")
    w._pills_edited()
    settle()
    assert len(w.catalog.pictures) == 4  # D.RAF + D.JPG is one picture, its card .xmp a companion
    for stem, names in WANTED.items():
        w.go_to(stem_index(w, stem))
        w.setFocus()
        for n in names:
            w.go_to(stem_index(w, stem))  # a pill on shows the next picture (0.16.0): come back for the second pill
            QTest.keyClick(w, Qt.Key_1 if n == "Hero" else Qt.Key_2)
        w.catalog.toggle_mark(stem_index(w, stem))
    before = {p: sha(p) for p in sorted(card.iterdir())}
    px = {n: Image.open(card / n).tobytes() for n in ("A.jpg", "B.jpg", "C.png", "D.JPG")}

    def do_copy() -> None:
        w.open_copy()
        sheet = w._sheet
        assert sheet is not None and sheet.isVisible()
        assert not sheet.pills_block.isHidden() and sheet.pill_counts()
        sheet.dest = dest
        sheet.organise = False  # flat, so the files sit where the test looks
        sheet.free_space_fn = lambda p: 10**12
        sheet._update_dest()
        sheet.start_copy()
        assert wait_for(lambda: sheet.result is not None, 20.0)
        assert not sheet.result.failed
        sheet.wait_idle()
        sheet.close()

    do_copy()
    yield w, card, dest, before, px, do_copy
    w.close()


def assert_readback(w2: MainWindow) -> None:
    assert w2.wait_pill_readback(15000)
    for stem, names in WANTED.items():
        assert pills_of(w2, stem) == set(names), stem
    assert {p.name: p.color for p in w2.pill_book.pills()} == {"Hero": "Blue", "Delivered": "Green"}
    for stem, names in WANTED.items():
        w2.go_to(stem_index(w2, stem))
        pump(lambda: set(lit(w2)) == set(names))
        assert w2.inspector.plain().endswith("Pills: " + " ".join(f"• {n}" for n in names)) or all(
            f"• {n}" in w2.inspector.plain() for n in names), (stem, w2.inspector.plain())
        assert "Pills:" in w2.inspector.plain()


def test_the_copy_carries_the_pills_and_a_new_window_shows_them(journey, tmp_path):
    w, card, dest, *_ = journey
    assert sorted(p.name for p in dest.iterdir()) == ["A.jpg", "B.jpg", "C.png", "D.JPG", "D.RAF", "D.xmp"]
    w2 = open_window(dest)
    try:
        assert_readback(w2)
    finally:
        w2.close()


def test_the_pills_come_from_the_files_not_the_index(journey, tmp_path, monkeypatch):
    w, card, dest, *_ = journey
    fresh = tmp_path / "a-fresh-empty-pills-dir"
    monkeypatch.setenv("SPUZA_PILLS_DIR", str(fresh))
    # Hold the read-back until the empty start has been asserted: on a fast machine the worker would otherwise
    # finish and have its batch applied before the assertion, which is a race, not a failure.
    started = []
    real_start = MainWindow.start_pill_readback
    monkeypatch.setattr(MainWindow, "start_pill_readback", lambda self: started.append(self))
    w2 = open_window(dest)
    try:
        assert started == [w2] and not w2._pill_ready
        assert w2.pill_book.pills() == []  # nothing known before the read-back
        assert pills_of(w2, "A") == set()
        real_start(w2)  # now let the read run
        assert_readback(w2)  # the book adopts both pills, colours snapped to the nearest swatch
        assert w2._pill_dirty
    finally:
        w2.close()
    assert (fresh / "pills.json").is_file()


def test_the_card_is_never_written(journey):
    w, card, dest, before, *_ = journey
    assert {p: sha(p) for p in sorted(card.iterdir())} == before
    assert sorted(p.name for p in card.iterdir()) == ["A.jpg", "B.jpg", "C.png", "D.JPG", "D.RAF", "D.xmp"]


def test_pixels_and_raw_bytes_survive(journey):
    w, card, dest, before, px, _ = journey
    for name, want in px.items():
        with Image.open(dest / name) as im:
            assert im.tobytes() == want, name
    assert sha(dest / "D.RAF") == before[card / "D.RAF"]  # a RAW is never rewritten
    assert xmp.parse_pills(xmp.read_embedded(dest / "A.jpg")) is not None


def test_the_foreign_keyword_in_the_card_sidecar_survives_in_the_copy(journey):
    w, card, dest, *_ = journey
    side = (dest / "D.xmp").read_bytes()
    assert b"FromCamera" in side and b"CameraX" in side and b"Hero" in side
    assert [t.name for t in xmp.parse_pills(side)] == ["Hero"]
    assert (card / "D.xmp").read_bytes() == FOREIGN


def test_a_second_copy_makes_no_renamed_duplicates(journey):
    w, card, dest, before, px, do_copy = journey
    names = sorted(p.name for p in dest.iterdir())
    do_copy()
    assert sorted(p.name for p in dest.iterdir()) == names
    assert {p: sha(p) for p in sorted(card.iterdir())} == before
    w2 = open_window(dest)
    try:
        assert_readback(w2)
    finally:
        w2.close()
