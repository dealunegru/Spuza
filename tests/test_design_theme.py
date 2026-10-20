"""The theme engine (design round 2): tokens -> Colors/palette/QSS, who decides the theme, retheme."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication

from spuza import features
from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.ui import settings, theme, tokens
from spuza.ui.compare import CompareView
from spuza.ui.filmstrip import THUMB_H, Filmstrip
from spuza.ui.viewer import Viewer
from spuza.ui.window import MainWindow

APP = QApplication.instance()
OLD_FIELDS = ("dark", "window", "panel", "base", "text", "dim", "border", "accent", "green", "amber", "stage")
NEW_FIELDS = ("raised", "hover", "pressed", "text_disabled", "focus", "on_focus", "marked", "rejected",
              "cropped", "warning", "hud", "key_chip_bg", "key_chip_text")


@pytest.fixture(autouse=True)
def clean_theme():
    theme._stop_following()
    yield
    theme._stop_following()
    theme._current = theme.DARK
    APP.setStyleSheet("")
    APP.setPalette(QPalette())


class FakeHints(QObject):
    colorSchemeChanged = Signal(object)

    def __init__(self):
        super().__init__()
        self.light = False


@pytest.fixture
def system(monkeypatch):
    """A system whose scheme the test flips: is_dark reads it; the signal is emitted by hand."""
    hints = FakeHints()
    monkeypatch.setattr(theme, "is_dark", lambda app=None: not hints.light)
    monkeypatch.setattr(APP, "styleHints", lambda: hints)
    return hints


def prefs():
    return settings.open_settings()


def test_every_token_grey_is_neutral_and_hud_parses():
    for mode in (tokens.DARK, tokens.LIGHT):
        for name, v in mode.items():
            if v.startswith("#"):
                c = QColor(v)
                grey = c.red() == c.green() == c.blue()
                if name in ("focus", "on_focus", "marked", "rejected", "cropped", "warning"):
                    continue  # state colours
                assert grey, (name, v)
            else:
                assert theme.qcolor(v).alpha() > 0 and theme.qcolor(v).red() == theme.qcolor(v).blue()


def test_colors_has_old_and_new_fields_mapped_from_tokens():
    for c, t in ((theme.DARK, tokens.DARK), (theme.LIGHT, tokens.LIGHT)):
        for f in OLD_FIELDS + NEW_FIELDS:
            assert hasattr(c, f), f
        assert (c.green, c.amber, c.accent) == (t["marked"], t["warning"], t["focus"])
        assert (c.dim, c.stage, c.window) == (t["text_dim"], t["stage"], t["window"])
        for name in t:
            assert getattr(c, name) == t[name], name
    assert theme.DARK.dark and not theme.LIGHT.dark


def test_apply_installs_palette_and_one_stylesheet():
    c = theme.apply(APP, False)
    assert c is theme.LIGHT and theme.current() is theme.LIGHT
    assert APP.palette().color(QPalette.Window).name() == theme.LIGHT.window
    sheet = APP.styleSheet()
    assert sheet == theme.qss(theme.LIGHT) and theme.LIGHT.focus in sheet and "#hud" in sheet
    theme.apply(APP, True)
    assert APP.palette().color(QPalette.Window).name() == theme.DARK.window and theme.DARK.hud in APP.styleSheet()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_start_without_pick_is_dark_and_does_not_follow(platform, system):
    system.light = True  # the system says light; off macOS it is ignored
    seen = []
    theme.start(APP, prefs(), lambda: seen.append(1), platform=platform)
    assert theme.current().dark and not theme.following() and seen == [1]
    system.light = False
    system.colorSchemeChanged.emit(None)
    system.light = True
    system.colorSchemeChanged.emit(None)
    assert theme.current().dark and seen == [1]


def test_start_on_darwin_follows_the_system_now_and_later(system):
    seen = []
    system.light = True
    theme.start(APP, prefs(), lambda: seen.append(theme.current().dark), platform="darwin")
    assert theme.following() and seen == [False]
    system.light = False
    system.colorSchemeChanged.emit(None)
    assert theme.current().dark and seen == [False, True]
    system.light = True
    system.colorSchemeChanged.emit(None)
    assert not theme.current().dark and seen == [False, True, False]


def test_choose_persists_and_stops_following(system, tmp_path):
    seen = []
    theme.start(APP, prefs(), platform="darwin")
    assert theme.following()
    theme.choose(APP, prefs(), False, lambda: seen.append(1))
    assert not theme.following() and not theme.current().dark and seen == [1]
    fresh = settings.open_settings()  # a second QSettings on the same file: it was written through
    assert fresh.value(settings.THEME) == "light" and theme.choice(fresh) == "light"
    system.light = False
    system.colorSchemeChanged.emit(None)  # the system flips: a pick is not overridden
    assert not theme.current().dark and seen == [1]


def test_start_after_a_pick_ignores_the_system(system):
    theme.choose(APP, prefs(), False)
    system.light = False  # the system is dark, the pick is light
    theme.start(APP, settings.open_settings(), platform="darwin")
    assert not theme.following() and not theme.current().dark
    theme.choose(APP, prefs(), True)
    system.light = True
    theme.start(APP, settings.open_settings(), platform="darwin")
    assert not theme.following() and theme.current().dark


@pytest.mark.parametrize("junk", ["", "blue", "DARKISH", None])
def test_junk_pref_is_no_pick(junk):
    p = prefs()
    if junk is not None:
        p.setValue(settings.THEME, junk)
    assert theme.choice(p) is None
    theme.start(APP, p, platform="linux")
    assert theme.current().dark


def test_old_follow_still_works(system):
    system.light = True
    theme.follow(APP)
    assert not theme.current().dark and theme.following()
    system.light = False
    system.colorSchemeChanged.emit(None)
    assert theme.current().dark


def test_stopping_twice_and_an_emit_after_stop_are_harmless(system):
    theme.start(APP, prefs(), platform="darwin")
    theme._stop_following()
    theme._stop_following()
    system.colorSchemeChanged.emit(None)
    assert not theme.following()


def _loader(path, max_side):
    raise ValueError("no picture")


def test_retheme_over_an_empty_catalog_does_not_raise():  # bug-437
    cat = Catalog([])
    pf = Prefetcher(cat, loader=_loader)
    w = MainWindow(cat, pf, Path("/nope"), resume=False)
    w.show()
    try:
        w.retheme()
        theme.apply(APP, False)
        w.retheme()
    finally:
        w.close()
        pf.shutdown()


def _pic(n):
    p = Path(f"/nope/P{n}.JPG")
    return Picture(p.stem, p, None, (p,), "JPG", 1, float(n))


def test_filmstrip_current_outline_is_neutral_and_restyles():
    class Cached:
        def cached(self, i):
            return None

    s = Filmstrip(Catalog([_pic(i) for i in range(4)]), Cached())
    s.resize(800, 120)
    s.show()
    s.set_current(0)
    for dark in (True, False):
        c = theme.apply(APP, dark)
        s.retheme()
        QApplication.processEvents()
        r = s.visualRect(s._model.index(0))
        img: QImage = s.viewport().grab().toImage()
        px = QColor(img.pixel(r.x() + 4 + 1, r.y() + 4 + THUMB_H // 2))
        assert px.name() == c.text and px.name() != c.focus
    s.close()


def test_viewer_restyles_on_light_dark():
    v = Viewer()
    v.resize(400, 300)
    v.show()
    seen = {}
    for dark in (True, False):
        c = theme.apply(APP, dark)
        v.retheme()
        QApplication.processEvents()
        seen[dark] = QColor(v.viewport().grab().toImage().pixel(2, 2)).name()
        assert seen[dark] == c.stage
        assert c.hud in v.hud.styleSheet() and c.hud in v.strip.styleSheet() and c.text_dim in v.message.styleSheet()
    assert seen[True] != seen[False]
    v.close()


def test_compare_restyles_on_light_dark():
    features.set_mode(features.ADVANCED)
    cat = Catalog([_pic(i) for i in range(3)])
    pf = Prefetcher(cat, ahead=1, behind=0, loader=_loader, threads=1)
    view = CompareView(cat, pf, loader=_loader)
    view.resize(900, 300)
    view.show()
    view.open([0, 1], focus=0)
    try:
        for dark in (True, False):
            c = theme.apply(APP, dark)
            view.retheme()
            cell = view.cell(0)
            assert cell.backgroundBrush().color().name() == c.stage
            assert view.border_colour(0) == c.focus and view.border_colour(1) == "transparent"
            assert c.hud in cell.tag.styleSheet() and c.text_dim in cell.message.styleSheet()
    finally:
        view.shutdown()
        pf.shutdown()
        view.close()
