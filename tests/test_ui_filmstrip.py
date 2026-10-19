"""Filmstrip: no prefetch-ready bars under the thumbnails; right-click reveals the picture under it."""

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza import reveal as rv
from spuza.catalog import Catalog, Picture
from spuza.ui import theme
from spuza.ui.filmstrip import THUMB_H, Filmstrip


def pic(n: int) -> Picture:
    p = Path(f"/nope/R{n:07d}.RAF")
    return Picture(p.stem, p, None, (p,), "RAF only", 1, float(n))


class Cached:
    def __init__(self, n):
        self.n = n

    def cached(self, i):
        if i >= self.n:
            return None
        img = QImage(64, 48, QImage.Format.Format_RGB888)
        img.fill(QColor("#303030"))
        return img


def make(n=20, cached=20):
    s = Filmstrip(Catalog([pic(i) for i in range(n)]), Cached(cached))
    s.resize(1200, 120)
    s.show()
    QApplication.processEvents()
    return s


def test_no_green_bar_under_upcoming_thumbnails():
    s = make()
    s.set_current(0)
    QApplication.processEvents()
    img = s.viewport().grab().toImage()
    green = QColor(theme.current().green).rgb()
    for i in range(1, 6):  # the next pictures, all cached
        r = s.visualRect(s._model.index(i))
        x, y = r.x() + 4 + 52, r.y() + 4 + THUMB_H + 1  # first row where the old bar was
        for dy in range(3):
            assert img.pixelColor(x, y + dy).rgb() != green, (i, dy)
    assert not hasattr(s, "is_ready_ahead")


def test_right_click_menu_on_thumbnail(monkeypatch):
    got = []
    monkeypatch.setattr(rv, "reveal", lambda p: got.append(p))
    s = make()
    s.set_current(2)
    picked = []
    s.picked.connect(picked.append)
    pos = s.visualRect(s._model.index(5)).center()
    menu = s.context_menu_for(pos)
    (action,) = menu.actions()
    assert action.text() == rv.reveal_label()
    action.trigger()
    assert got == [Path("/nope/R0000005.RAF")]
    QTest.mouseClick(s.viewport(), Qt.MouseButton.RightButton, pos=pos)
    assert picked == [] and s.current == 2  # right-click does not move the current picture
    QTest.mouseClick(s.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert picked == [5]


def test_no_menu_on_empty_space():
    s = make(n=3)
    assert s.context_menu_for(QPoint(1100, 40)) is None


def test_edit_tags_are_a_list_a_bare_string_is_one_crop_tag_and_junk_is_none():
    s = make(3)
    assert s.edit_tags(0) == []
    s.edit_tag_of = lambda i: [("4:5", "crop"), ("9:16", "story")]
    assert s.edit_tags(0) == [("4:5", "crop"), ("9:16", "story")]
    s.edit_tag_of = lambda i: "crop"
    assert s.edit_tags(0) == [("crop", "crop")]
    s.edit_tag_of = lambda i: [("x", "weird")]
    assert s.edit_tags(0) == [("x", "crop")]
    for bad in (5, [5], [("a",)], object()):
        s.edit_tag_of = lambda i, b=bad: b
        assert s.edit_tags(0) == []
    s.edit_tag_of = lambda i: 1 / 0
    assert s.edit_tags(0) == []


def test_two_tags_paint_side_by_side_the_story_one_as_an_outline():
    s = make(3)
    s.edit_tag_of = lambda i: [("4:5", "crop"), ("9:16", "story")]
    s.set_current(1)
    cell = s.visualRect(s.model().index(0))
    img = s.viewport().grab(cell).toImage()
    c = theme.current()
    row = 10  # inside the tag row (box.y + 3 .. + 16, cell offset 4)
    seen = [QColor(img.pixel(x, row)).name() for x in range(img.width())]
    assert c.accent.lower() in seen  # the filled crop tag and the outline's border are both accent
    assert c.panel.lower() in seen  # the outline tag's panel fill
