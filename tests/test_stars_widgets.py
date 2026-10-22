"""The 1-5 star display widgets: stars_text, StarBar, the inspector row, filmstrip stars and the Lights-out HUD."""

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza.catalog import Catalog, Picture
from spuza.ui import star_bar, theme
from spuza.ui.filmstrip import CELL_H, THUMB_H, Filmstrip
from spuza.ui.inspector import Inspector
from spuza.ui.pill import LightsOutPill
from spuza.ui.star_bar import StarBar, stars_text


def test_stars_text_values_and_clamp():
    assert [stars_text(n) for n in range(6)] == ["☆☆☆☆☆", "★☆☆☆☆", "★★☆☆☆", "★★★☆☆", "★★★★☆", "★★★★★"]
    assert stars_text(-3) == "☆☆☆☆☆" and stars_text(99) == "★★★★★"
    assert stars_text(None) == "☆☆☆☆☆" and stars_text("x") == "☆☆☆☆☆"  # junk is unrated, not a crash


def _click(bar, n):
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     bar.star_rect(n).center().toPoint())


def test_starbar_click_emits_n_and_lit_top_star_clears():
    bar = StarBar()
    got = []
    bar.rated.connect(got.append)
    bar.show()
    _click(bar, 3)
    assert got == [3]
    bar.set_stars(3)
    _click(bar, 3)  # the lit top star
    _click(bar, 2)  # a lower lit star is just a click on 2
    _click(bar, 5)
    assert got == [3, 0, 2, 5]
    bar.set_stars(0)
    _click(bar, 1)
    assert got[-1] == 1


def test_starbar_set_stars_is_silent_clamped_and_fixed_width():
    bar = StarBar()
    got = []
    bar.rated.connect(got.append)
    bar.set_stars(4)
    assert bar.stars() == 4
    bar.set_stars(9)
    assert bar.stars() == 5
    bar.set_stars(-1)
    assert bar.stars() == 0
    assert got == []
    assert bar.width() == bar.minimumWidth() == bar.maximumWidth() == 180
    assert "Ctrl+" in star_bar.tip_for(3) or "⌘" in star_bar.tip_for(3)
    assert star_bar.tip_for(3).startswith("3 stars") and star_bar.tip_for(1).startswith("1 star ")


def test_starbar_paints_lit_stars_in_text_colour_and_others_dimmer():
    bar = StarBar()
    bar.set_stars(2)
    bar.show()
    img = bar.grab().toImage()
    c = theme.current()

    def colors_at(n):
        x, y = int(bar.star_rect(n).center().x()), int(bar.star_rect(n).center().y())
        return QColor(img.pixel(x, y)).name()

    assert colors_at(1) == colors_at(2) == QColor(c.text).name()  # a filled star's centre is the text colour
    assert colors_at(4) != QColor(c.text).name()  # an empty star's centre is not filled


def test_star_bar_has_no_hex_literal():
    src = Path(star_bar.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", src)
    assert "yellow" not in src.lower() and "gold" not in src.lower()


def _pic() -> Picture:
    p = Path("IMG_0001.JPG")
    return Picture("IMG_0001", p, None, (p,), "JPG", 1000, 0.0)


def test_inspector_stars_row_after_pills():
    w = Inspector()
    w.set_picture(_pic(), None)
    assert "Stars: not rated" in w.label.text()
    w.set_pills([("Hero", "Red")])
    w.set_stars(4)
    html = w.label.text()
    assert w.stars_line() == "Stars: ★★★★☆ 4 of 5"
    assert "★★★★☆ 4 of 5" in html and html.index("Pills:") < html.index("Stars:")
    assert "Stars" not in w.plain()  # plain() keeps its long-pinned shape: it ends with the pills row
    w.set_stars(0)
    assert "not rated" in w.label.text() and "of 5" not in w.label.text()
    w.set_stars(99)
    assert "★★★★★ 5 of 5" in w.label.text()


def test_inspector_stars_default_unrated_and_without_picture():
    w = Inspector()
    assert w.stars == 0
    w.set_stars(3)  # no picture yet: nothing to draw, no crash
    w.set_picture(_pic(), None)
    assert "3 of 5" in w.label.text()


# ---- filmstrip ----

def _fpic(n: int) -> Picture:
    p = Path(f"/nope/R{n:07d}.RAF")
    return Picture(p.stem, p, None, (p,), "RAF only", 1, float(n))


class _Cached:
    def cached(self, i):
        img = QImage(64, 48, QImage.Format.Format_RGB888)
        img.fill(QColor("#303030"))
        return img


def _strip():
    s = Filmstrip(Catalog([_fpic(i) for i in range(3)]), _Cached())
    s.resize(600, CELL_H + 30)
    s.show()
    QApplication.processEvents()
    return s


def _cell(s, row=0):
    return s.viewport().grab(s.visualRect(s.model().index(row))).toImage()


def _band(img):
    """Pixels of the free band under the thumb: cell offset 4, thumb 62 high, so y 67 .. 80."""
    y0 = 4 + THUMB_H + 1
    return [img.pixel(x, y) for y in range(y0, y0 + 13) for x in range(4, 70)]


def test_filmstrip_default_stars_of_is_zero_and_draws_nothing():
    s = _strip()
    assert s.stars_of(0) == 0
    before = _cell(s)
    s.stars_of = lambda i: 0
    s.item_changed(0)
    QApplication.processEvents()
    assert _cell(s) == before


def test_filmstrip_draws_stars_in_the_band_only_when_rated_and_keeps_badges():
    s = _strip()
    s.stars_of = lambda i: 3 if i == 0 else 0
    s.item_changed(0)
    s.item_changed(1)
    QApplication.processEvents()
    plain = _cell(s, 1)
    rated = _cell(s, 0)
    text = QColor(theme.current().text).rgb()
    assert text in _band(rated) and text not in _band(plain)
    no_stars = _strip()
    base = _cell(no_stars, 0)
    y0 = 4 + THUMB_H
    for y in range(0, y0):  # everything over the thumb (the four corner badges) is unchanged
        for x in range(rated.width()):
            assert rated.pixel(x, y) == base.pixel(x, y)


def test_filmstrip_bad_stars_provider_draws_nothing():
    s = _strip()
    base = _cell(s, 0)
    s.stars_of = lambda i: 1 / 0
    s.item_changed(0)
    QApplication.processEvents()
    assert _cell(s, 0) == base
    s.stars_of = lambda i: "junk"
    s.item_changed(0)
    QApplication.processEvents()
    assert _cell(s, 0) == base


# ---- Lights out ----

def test_lights_out_pill_stars_take_width_only_when_rated():
    w = LightsOutPill()
    w.set_state(False, "3 / 9")
    w.show()
    w0 = w.width()
    w.set_stars(0)
    assert w.width() == w0
    w.set_stars(3)
    assert w.width() > w0
    assert w.stars_text() == "★★★"
    w.set_stars(0)
    assert w.width() == w0 and w.stars_text() == ""
