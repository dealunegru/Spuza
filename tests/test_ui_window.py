"""MainWindow driven with QTest key events, offscreen. Decoding is faked; no files are read."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui.window import MainWindow

# A QApplication must exist before any other test file's fixture makes a bare QGuiApplication
# (test_catalog does, and then widgets abort). Collection imports this module first, so create it here.
APP = QApplication.instance() or QApplication([])

SIZE = 28_000_000
ROOT = Path("/cards/X-PRO2")


def pic(name: str, sidecar: bool = False) -> Picture:
    p = ROOT / name
    side = ROOT / (p.stem + ".JPG") if sidecar else None
    files = (p,) + ((side,) if side else ())
    kind = (p.suffix[1:].upper() + ("+JPG" if sidecar else " only")) if p.suffix == ".RAF" else "JPG"
    return Picture(p.stem, p, side, files, kind, SIZE, 1.0)


PICS = [pic("A0.RAF", True), pic("A1.RAF"), pic("A2.JPG"), pic("bad3.JPG"), pic("A4.JPG"), pic("A5.RAF", True)]


def fake_loader(path, max_side):
    if "bad" in path.name:
        raise ValueError("corrupt file")
    return Image.new("RGB", (64, 48), (200, 80, 40)), PreviewInfo(path, "raf-embedded", 1920, 1280, 1000, 3.5)


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        QTest.qWait(5)
    assert cond(), "condition not reached in time"


class FakeReview(QWidget):
    open_index = Signal(int)
    marks_changed = Signal()
    closed = Signal()

    def __init__(self, catalog, prefetcher=None, parent=None):
        super().__init__(parent)
        self.catalog, self.refreshed = catalog, 0

    def refresh(self):
        self.refreshed += 1


@pytest.fixture
def clock():
    box = [100.0]

    def now():
        return box[0]
    now.box = box
    return now


@pytest.fixture
def win(clock):
    cat = Catalog(PICS)
    pf = Prefetcher(cat, loader=fake_loader)
    w = MainWindow(cat, pf, ROOT, clock=clock)
    w.review_factory = lambda catalog, prefetcher=None, parent=None: FakeReview(catalog, prefetcher, parent)
    w.resize(1200, 800)
    w.show()
    pump(lambda: w.viewer.has_image)
    yield w
    w.close()


def key(w, k):
    QTest.keyClick(w, k)


def test_keys_move_index_and_status(win):
    assert win.lbl_pos.text() == "1 of 6"
    key(win, Qt.Key_Right)
    assert win.catalog.index == 1 and win.lbl_pos.text() == "2 of 6"
    key(win, Qt.Key_J)
    assert win.catalog.index == 2
    key(win, Qt.Key_Left)
    key(win, Qt.Key_K)
    assert win.catalog.index == 0 and win.lbl_pos.text() == "1 of 6"
    key(win, Qt.Key_Left)  # clamps
    assert win.catalog.index == 0


def test_toolbar_has_every_key_with_its_cap(win):
    caps = {n: b.cap for n, b in win.buttons.items()}
    assert caps == {"back": "←/K", "next": "→/J", "mark": "Space/M", "advance": "A", "zoom": "Z",
                    "fullscreen": "F", "review": "R", "copy": "C"}
    assert win.buttons["zoom"].text().startswith("Zoom 100%")
    for b in win.buttons.values():
        assert b.cap in b.text()


def test_space_marks_and_lights_button_and_counts(win):
    mark = win.buttons["mark"]
    assert not mark.isChecked() and win.lbl_marked.text() == "0 marked · 0 B"
    key(win, Qt.Key_Space)
    assert mark.isChecked() and win.catalog.is_marked(0)
    assert win.lbl_marked.text() == "1 marked · 28 MB"
    key(win, Qt.Key_Right)
    assert not mark.isChecked()
    key(win, Qt.Key_M)
    key(win, Qt.Key_Right)
    key(win, Qt.Key_M)
    assert win.lbl_marked.text() == "3 marked · 84 MB"
    key(win, Qt.Key_M)  # unmark
    assert win.lbl_marked.text() == "2 marked · 56 MB"
    key(win, Qt.Key_Left)
    assert mark.isChecked()  # the lit state follows the current picture
    assert win.sidebar.counts() == {"All": 6, "Marked": 2}


def test_mark_button_click_marks(win):
    win.buttons["mark"].click()
    assert win.catalog.is_marked(0) and win.buttons["mark"].isChecked()


def test_sidebar_all_above_marked(win):
    assert win.sidebar.row_names() == ["All", "Marked"]
    assert win.sidebar.root_text().endswith("X-PRO2")


def test_ready_and_elapsed_are_live(win, clock):
    pump(lambda: win.lbl_ready.text() != "next 0 ready")
    assert win.lbl_ready.text() == f"next {win.prefetcher.ready_ahead()} ready"
    assert win.lbl_elapsed.text() == "00:00"
    clock.box[0] += 75
    win.refresh_status()
    assert win.lbl_elapsed.text() == "01:15"


def test_full_bleed_toggle_and_esc(win):
    key(win, Qt.Key_F)
    assert win.is_bleed
    for part in (win.toolbar, win.sidebar, win.inspector, win.filmstrip, win.statusBar()):
        assert part.isHidden()
    assert not win.viewer.strip.isHidden()
    assert "1 of 6" in win.viewer.strip.text()
    key(win, Qt.Key_Escape)
    assert not win.is_bleed
    for part in (win.toolbar, win.sidebar, win.inspector, win.filmstrip, win.statusBar()):
        assert not part.isHidden()
    assert win.viewer.strip.isHidden()


def test_bleed_strip_follows_mark(win):
    key(win, Qt.Key_F)
    key(win, Qt.Key_Space)
    assert "1 marked" in win.viewer.strip.text() and win.viewer.strip.marked
    assert "A0" in win.viewer.strip.text() and "RAF+JPG" in win.viewer.strip.text()


def test_review_factory_and_return(win):
    key(win, Qt.Key_R)
    assert win.stack.currentIndex() == 1 and isinstance(win.review, FakeReview)
    win.review.open_index.emit(4)
    assert win.stack.currentIndex() == 0 and win.catalog.index == 4
    key(win, Qt.Key_R)
    key(win, Qt.Key_Escape)
    assert win.stack.currentIndex() == 0
    key(win, Qt.Key_R)
    key(win, Qt.Key_R)
    assert win.stack.currentIndex() == 0
    assert win.review.refreshed == 3


def test_copy_with_nothing_marked_shows_message(win):
    calls = []
    win.copy_sheet_factory = lambda *a, **k: calls.append((a, k))
    key(win, Qt.Key_C)
    assert not calls and "mark" in win.last_message.lower()


def test_copy_opens_sheet_with_marked_and_default_dest(win):
    seen = {}

    class Sheet(QObject):
        def __init__(self, pictures, default_dest=None, parent=None):
            super().__init__(parent)
            seen.update(pictures=pictures, dest=default_dest, parent=parent)

        def open(self):
            seen["opened"] = True

    win.copy_sheet_factory = Sheet
    key(win, Qt.Key_M)
    key(win, Qt.Key_C)
    assert seen["pictures"] == [PICS[0]] and seen["dest"] == ROOT / "Spuza"
    assert seen["parent"] is win and seen["opened"]


def test_failed_preview_shows_reason_and_never_blocks(win):
    for _ in range(3):
        key(win, Qt.Key_Right)
    pump(lambda: win.viewer.error_text)
    assert "corrupt file" in win.viewer.error_text
    assert win.catalog.index == 3
    key(win, Qt.Key_Right)
    assert win.catalog.index == 4
    pump(lambda: win.viewer.has_image and not win.viewer.error_text)


def test_stepping_shows_cached_image_at_once(win):
    pump(lambda: win.prefetcher.cached(1) is not None)
    key(win, Qt.Key_Right)
    assert win.viewer.has_image and win.viewer.index == 1  # no event loop turn in between


def test_zoom_decodes_off_thread_and_swaps(win):
    main = threading.get_ident()
    seen = {}

    def full(path, half_size=False):
        seen["thread"] = threading.get_ident()
        return Image.new("RGB", (200, 100), (1, 2, 3))

    win.viewer.needs_full_decode = lambda p, info: True
    win.viewer.full_decode = full
    key(win, Qt.Key_Z)
    assert win.viewer.zoomed and win.buttons["zoom"].isChecked()
    pump(lambda: win.viewer.image_size() == (200, 100))
    assert seen["thread"] != main
    assert not win.viewer.hud_visible
    key(win, Qt.Key_Z)
    assert not win.viewer.zoomed and not win.buttons["zoom"].isChecked()


def test_zoom_skips_decode_when_preview_is_enough(win):
    calls = []
    win.viewer.needs_full_decode = lambda p, info: False
    win.viewer.full_decode = lambda *a, **k: calls.append(a)
    key(win, Qt.Key_Z)
    pump(lambda: win.viewer.full_state(0) == "skipped")
    assert not calls and win.viewer.image_size() == (64, 48)


def test_full_decode_result_dropped_when_picture_changed(win):
    gate, started = threading.Event(), threading.Event()

    def slow(path, half_size=False):
        started.set()
        gate.wait(10)
        return Image.new("RGB", (200, 100))

    win.viewer.needs_full_decode = lambda p, info: p.suffix == ".RAF"
    win.viewer.full_decode = slow
    key(win, Qt.Key_Z)
    pump(lambda: win.viewer.hud_visible and started.is_set())
    key(win, Qt.Key_Right)
    key(win, Qt.Key_Right)  # A2.JPG: zoom carries, but a JPG needs no full decode
    gate.set()
    pump(lambda: win.viewer.full_state(0) == "done")
    pump(lambda: not win.viewer.hud_visible)
    assert win.viewer.index == 2 and win.viewer.image_size() == (64, 48) and win.viewer.zoomed


def test_empty_catalog_shows_message(clock):
    cat = Catalog([])
    w = MainWindow(cat, Prefetcher(cat, loader=fake_loader), ROOT, clock=clock)
    w.show()
    key(w, Qt.Key_Right)
    key(w, Qt.Key_Space)
    assert w.lbl_pos.text() == "0 of 0" and "No pictures" in w.viewer.error_text
    w.close()


def test_inspector_explains_pairs(win):
    text = win.inspector.plain()
    assert "RAF + JPG: one picture, both files copy together" in text
    assert "A0.RAF" in text and "A0.JPG" in text and "28 MB" in text
    key(win, Qt.Key_Right)
    assert "RAF only: no JPG beside it" in win.inspector.plain()
    assert win.inspector.amber
    pump(lambda: "raf-embedded" in win.inspector.plain())
    assert "1920" in win.inspector.plain() and "3.5 ms" in win.inspector.plain()
    key(win, Qt.Key_M)
    assert "Marked" in win.inspector.plain() and "Not marked" not in win.inspector.plain()


def test_filmstrip_click_goes_to_picture(win):
    win.filmstrip.picked.emit(4)
    assert win.catalog.index == 4
    assert win.filmstrip.current == 4


def test_filmstrip_2000_pictures_is_cheap(clock):
    many = [pic(f"P{i}.JPG") for i in range(2000)]
    cat = Catalog(many)
    w = MainWindow(cat, Prefetcher(cat, loader=fake_loader), ROOT, clock=clock)
    w.resize(1200, 800)
    t0 = time.perf_counter()
    w.show()
    for _ in range(30):
        key(w, Qt.Key_Right)
    assert time.perf_counter() - t0 < 5
    assert w.filmstrip.model().rowCount() == 2000
    w.close()


def test_theme_follows_scheme_switch(win):
    from spuza.ui import theme

    for dark in (True, False):
        theme.apply(APP, dark)
        win.retheme()
        assert theme.current().dark is dark
        assert win.grab().width() > 0


def test_message_in_bleed_shows_on_strip_and_expires(win):
    key(win, Qt.Key_F)
    key(win, Qt.Key_C)  # nothing marked
    note = win.viewer.strip.note
    assert not note.isHidden() and "marked" in note.text().lower()
    win.viewer.strip.expire_note()  # what the single-shot timer does after 5 s
    assert note.isHidden()


def test_message_outside_bleed_goes_to_status_bar(win):
    key(win, Qt.Key_C)
    assert "marked" in win.statusBar().currentMessage().lower()
    assert win.viewer.strip.note.isHidden()


# -- move on mark ---------------------------------------------------------------------------------
def test_move_on_mark_is_off_by_default_and_marking_stays(win):
    assert not win.advance_on_mark and not win.buttons["advance"].isChecked()
    key(win, Qt.Key_Space)
    assert win.catalog.is_marked(0) and win.catalog.index == 0


def test_a_toggles_move_on_mark_and_says_so(win):
    key(win, Qt.Key_A)
    assert win.advance_on_mark and win.buttons["advance"].isChecked()
    assert win.last_message.startswith("Move on mark: on")
    key(win, Qt.Key_A)
    assert not win.advance_on_mark and not win.buttons["advance"].isChecked()
    assert win.last_message == "Move on mark: off"
    win.buttons["advance"].click()
    assert win.advance_on_mark and win.buttons["advance"].isChecked()


def test_marking_moves_on_but_unmarking_does_not(win):
    key(win, Qt.Key_A)
    key(win, Qt.Key_Space)  # marks 0, shows 1
    assert win.catalog.is_marked(0) and win.catalog.index == 1
    key(win, Qt.Key_M)  # marks 1, shows 2
    assert win.catalog.is_marked(1) and win.catalog.index == 2
    key(win, Qt.Key_Left)  # back to 1 ...
    key(win, Qt.Key_Space)  # ... unmark it: stays, so the decision is visible
    assert not win.catalog.is_marked(1) and win.catalog.index == 1
    assert win.lbl_marked.text().startswith("1 marked")


def test_marking_the_last_picture_stays_on_it(win):
    key(win, Qt.Key_A)
    win.go_to(5)
    key(win, Qt.Key_Space)
    assert win.catalog.is_marked(5) and win.catalog.index == 5


def test_mark_button_click_also_moves_on(win):
    key(win, Qt.Key_A)
    win.buttons["mark"].click()
    assert win.catalog.is_marked(0) and win.catalog.index == 1


def test_move_on_mark_does_not_move_the_viewer_from_review(win):
    key(win, Qt.Key_A)
    key(win, Qt.Key_Space)  # mark 0 -> on 1
    key(win, Qt.Key_R)  # the fake review ignores keys; the hidden viewer must not move either
    key(win, Qt.Key_Space)
    assert win.stack.currentIndex() == 1 and win.catalog.index == 1
    win.toggle_mark()  # even a direct call while review is up marks in place, no advance
    assert win.catalog.is_marked(1) and win.catalog.index == 1
    key(win, Qt.Key_A)  # the toggle itself is a window shortcut: works from review (real grid: controls test)
    assert win.stack.currentIndex() == 1 and not win.advance_on_mark


def test_held_space_marks_one_picture_not_a_run(win):
    key(win, Qt.Key_A)
    key(win, Qt.Key_Space)  # the real press: marks 0, shows 1
    for _ in range(4):  # the OS repeats while the key is held
        QApplication.sendEvent(win, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Space,
                                              Qt.KeyboardModifier.NoModifier, " ", True, 1))
    assert win.catalog.marked_count == 1 and win.catalog.index == 1
    for _ in range(3):  # a held A does not flip the preference either
        QApplication.sendEvent(win, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_A,
                                              Qt.KeyboardModifier.NoModifier, "a", True, 1))
    assert win.advance_on_mark


def test_move_on_mark_persists_across_windows(clock):
    cat = Catalog(PICS)
    pf = Prefetcher(cat, loader=fake_loader)
    w = MainWindow(cat, pf, ROOT, clock=clock)
    w.set_advance_on_mark(True)
    w.close()
    w.deleteLater()
    QTest.qWait(0)
    assert "advance_on_mark=true" in Path(os.environ["SPUZA_SETTINGS_FILE"]).read_text()  # on disk, not
    cat2 = Catalog(PICS)                                                                   # a shared cache
    pf2 = Prefetcher(cat2, loader=fake_loader)
    w2 = MainWindow(cat2, pf2, ROOT, clock=clock)
    try:
        assert w2.advance_on_mark and w2.buttons["advance"].isChecked()
    finally:
        w2.close()
