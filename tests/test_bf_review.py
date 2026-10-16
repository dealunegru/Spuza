"""Bugfinder round 1: ReviewGrid zoom staleness (B7), zoom decode after Esc (B8), key auto-repeat (B9)."""

import threading

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem

from spuza.ui.review import ZOOM_SIDE, PAGE_ZOOM, ReviewGrid
from test_ui_review import catalog, qapp, wait_for  # noqa: F401  (qapp: fixture re-export)

COLORS = {"R0000001": (250, 0, 0), "R0000003": (0, 250, 0), "R0000005": (0, 0, 250)}


class GatedLoader:
    """Thumbnail decodes return at once; ZOOM_SIDE decodes block until released per picture name."""

    def __init__(self):
        self.gates = {n: threading.Event() for n in COLORS}
        self.zoom_calls = []

    def __call__(self, path, max_side):
        name = path.stem
        if max_side == ZOOM_SIDE:
            self.zoom_calls.append(name)
            assert self.gates[name].wait(10)
        col = COLORS[name] if max_side == ZOOM_SIDE else tuple(c // 2 for c in COLORS[name])  # thumb is darker
        return Image.new("RGB", (40, 30), col), None

    def release_all(self):
        for e in self.gates.values():
            e.set()


def build(qapp):  # noqa: F811
    ld = GatedLoader()
    g = ReviewGrid(catalog(), None, loader=ld)
    g.resize(900, 600)
    g.show()
    return g, ld


def scene_pixel(g):
    items = [i for i in g.scene.items() if isinstance(i, QGraphicsPixmapItem)]
    if not items:
        return None
    c = items[0].pixmap().toImage().pixelColor(1, 1)
    return (c.red(), c.green(), c.blue())


def full(name):
    return COLORS[name]


def select_row(g, row):
    g.view.setCurrentIndex(g.model.index(row, 0))


def test_zoom_clears_previous_picture_when_new_has_no_image(qapp):  # noqa: F811
    g, ld = build(qapp)
    g._thumbs.clear()
    g._queue.clear()
    select_row(g, 0)  # picture 1
    ld.gates["R0000001"].set()
    g.show_zoom()
    assert wait_for(lambda: scene_pixel(g) == COLORS["R0000001"])
    g.leave_zoom()
    g._thumbs.pop(3, None)  # B has no thumbnail, no cache, no zoom image
    select_row(g, 1)  # picture 3
    g.show_zoom()
    assert scene_pixel(g) != COLORS["R0000001"]
    ld.release_all()
    g.close()
    g.wait_idle()


def test_zoom_after_escape_still_decodes_new_picture(qapp):  # noqa: F811
    g, ld = build(qapp)
    select_row(g, 0)
    g.show_zoom()  # A: decode in flight (blocked)
    assert wait_for(lambda: "R0000001" in ld.zoom_calls)
    g.leave_zoom()
    select_row(g, 1)
    g.show_zoom()  # B
    ld.release_all()
    assert wait_for(lambda: scene_pixel(g) == COLORS["R0000003"])
    assert g._zoom_index == 3 and g.stack.currentIndex() == PAGE_ZOOM
    g.wait_idle()
    g.close()


def press(g, key, text, auto):
    ev = QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text, auto)
    QApplication.sendEvent(g.view, ev)


def test_auto_repeat_does_not_unmark_or_open(qapp):  # noqa: F811
    g, ld = build(qapp)
    ld.release_all()
    opened = []
    g.open_index.connect(opened.append)
    for _ in range(4):
        press(g, Qt.Key.Key_M, "m", True)
        press(g, Qt.Key.Key_Space, " ", True)
        press(g, Qt.Key.Key_Return, "\r", True)
    assert g.catalog.marked_count == 3
    assert opened == []
    press(g, Qt.Key.Key_M, "m", False)
    assert g.catalog.marked_count == 2
    press(g, Qt.Key.Key_Return, "\r", False)
    assert len(opened) == 1
    g.wait_idle()
    g.close()
