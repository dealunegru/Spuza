"""Friend round 2: "move on mark" also advances on unmark; the full-bleed strip is hidden until the pointer moves."""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_ui_window import PICS, ROOT, fake_loader, key, pump  # noqa: F401  (also makes the QApplication)

from spuza.catalog import Catalog, Prefetcher
from spuza.ui.window import MainWindow


@pytest.fixture
def win():
    cat = Catalog(PICS)
    w = MainWindow(cat, Prefetcher(cat, loader=fake_loader), ROOT, clock=lambda: 100.0)
    w.resize(1200, 800)
    w.show()
    pump(lambda: w.viewer.has_image)
    yield w
    w.close()


def move(w, x=50, y=50):
    """A plain pointer move (no button) delivered to the viewer's viewport."""
    vp = w.viewer.viewport()
    pos = QPointF(x, y)
    ev = QMouseEvent(QEvent.Type.MouseMove, pos, vp.mapToGlobal(pos), Qt.MouseButton.NoButton,
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(vp, ev)


# -- item 1: move on mark, both ways -----------------------------------------------------------------
def test_unmark_advances_with_move_on_mark_on(win):
    win.set_advance_on_mark(True, announce=False)
    win.catalog.toggle_mark(0)  # picture 0 already marked, we are on it
    assert win.catalog.index == 0
    win.toggle_mark()  # unmark
    assert not win.catalog.is_marked(0) and win.catalog.index == 1


def test_unmark_stays_with_move_on_mark_off(win):
    win.catalog.toggle_mark(0)
    win.toggle_mark()
    assert not win.catalog.is_marked(0) and win.catalog.index == 0


def test_mark_still_advances_and_off_stays(win):
    win.set_advance_on_mark(True, announce=False)
    win.toggle_mark()
    assert win.catalog.is_marked(0) and win.catalog.index == 1
    win.set_advance_on_mark(False, announce=False)
    win.toggle_mark()
    assert win.catalog.is_marked(1) and win.catalog.index == 1


def test_unmark_on_the_last_picture_stays(win):
    win.set_advance_on_mark(True, announce=False)
    win.go_to(5)
    win.catalog.toggle_mark(5)
    win.toggle_mark()
    assert not win.catalog.is_marked(5) and win.catalog.index == 5


# -- item 2: the full-bleed strip ----------------------------------------------------------------------
def test_viewer_tracks_the_mouse(win):
    assert win.viewer.hasMouseTracking() and win.viewer.viewport().hasMouseTracking()
    assert win.viewer._reveal_timer.isSingleShot()


def test_strip_hidden_on_entering_bleed(win):
    win.set_bleed(True)
    assert win.is_bleed and win.viewer.strip.isHidden()


def test_mouse_move_in_bleed_shows_then_timer_hides(win):
    win.set_bleed(True)
    move(win)
    assert not win.viewer.strip.isHidden() and win.viewer._reveal_timer.isActive()
    win.viewer._reveal_timer.timeout.emit()  # what the single shot does after the quiet period
    assert win.viewer.strip.isHidden()


def test_strip_really_hides_after_the_quiet_period(win):
    win.viewer.reveal_ms = 60
    win.set_bleed(True)
    move(win)
    assert not win.viewer.strip.isHidden()
    end = time.monotonic() + 5.0  # bounded wait
    while not win.viewer.strip.isHidden() and time.monotonic() < end:
        QTest.qWait(10)
    assert win.viewer.strip.isHidden()


def test_further_moves_restart_the_timer(win):
    win.viewer.reveal_ms = 100_000
    win.set_bleed(True)
    move(win, 10, 10)
    move(win, 20, 20)
    assert win.viewer._reveal_timer.isActive() and not win.viewer.strip.isHidden()


def test_say_in_bleed_shows_the_strip_then_it_hides(win):
    win.set_bleed(True)
    win.say("hello")
    assert not win.viewer.strip.isHidden() and win.viewer.strip.note.text() == "hello"
    win.viewer._reveal_timer.timeout.emit()
    assert win.viewer.strip.isHidden()


def test_stepping_alone_shows_nothing(win):
    win.set_bleed(True)
    key(win, Qt.Key_Right)
    key(win, Qt.Key_Left)
    assert win.viewer.strip.isHidden() and not win.viewer._reveal_timer.isActive()


def test_leaving_bleed_hides_and_stops_the_timer(win):
    win.set_bleed(True)
    move(win)
    win.set_bleed(False)
    assert win.viewer.strip.isHidden() and not win.viewer._reveal_timer.isActive()


def test_mouse_move_outside_bleed_does_nothing(win):
    move(win)
    assert win.viewer.strip.isHidden() and not win.viewer._reveal_timer.isActive()
    win.say("hi")  # status bar, not the strip
    assert win.viewer.strip.isHidden()

