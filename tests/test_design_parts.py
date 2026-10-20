"""Standalone restyle parts: bundled icons, Quiet bar, Lights-out pill (not wired into the window)."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QToolButton, QWidget

from spuza.ui import icons, theme
from spuza.ui.pill import LightsOutPill
from spuza.ui.quiet_bar import QuietBar

NEEDED = ["arrow-left", "arrow-right", "circle-check", "zoom-in", "layout-grid", "ellipsis",
          "copy", "keyboard", "maximize", "crop", "folder-open"]


@pytest.fixture
def restore_dark():
    yield
    theme.apply(QApplication.instance(), True)
    icons.clear_cache()


def _opaque_colors(pm):
    img = pm.toImage().convertToFormat(pm.toImage().Format.Format_ARGB32)
    seen = set()
    for y in range(img.height()):
        for x in range(img.width()):
            c = QColor.fromRgba(img.pixel(x, y))
            if c.alpha() == 255:
                seen.add(c.name())
    return seen


def test_all_needed_icons_and_license_bundled():
    assert set(NEEDED) <= set(icons.names())
    from importlib import resources
    lic = resources.files("spuza").joinpath("assets", "icons", "LICENSE").read_text(encoding="utf-8")
    assert "ISC" in lic


def test_icon_renders_requested_colour_at_dpr():
    pm = icons.pixmap("copy", "#ff0000", 18, 2.0)
    assert (pm.width(), pm.height()) == (36, 36) and pm.devicePixelRatio() == 2.0
    opaque = _opaque_colors(pm)
    assert opaque == {"#ff0000"}
    assert not icons.icon("copy", "#00ff00", 18, 1.0).pixmap(18, 18).isNull()


def test_unknown_icon_raises_keyerror():
    with pytest.raises(KeyError):
        icons.icon("nope", "#fff", 18, 1.0)


def test_cache_returns_same_pixmap():
    assert icons.pixmap("crop", "#123456", 18, 1.0) is icons.pixmap("crop", "#123456", 18, 1.0)


def test_themed_icon_disabled_uses_text_disabled():
    class C:
        text = "#e6e6e6"
        text_disabled = "#5e5e5e"
    ic = icons.themed_icon("copy", C, 18, 1.0)
    dis = ic.pixmap(18, 18, QIcon.Mode.Disabled)
    nor = ic.pixmap(18, 18, QIcon.Mode.Normal)
    assert _opaque_colors(dis) == {"#5e5e5e"}
    assert _opaque_colors(nor) == {"#e6e6e6"}



def test_themed_icon_works_with_old_colors_without_new_fields():
    class Old:
        text = "#abcdef"
    ic = icons.themed_icon("copy", Old, 18, 1.0)
    assert not ic.pixmap(18, 18, QIcon.Mode.Disabled).isNull()


def test_bar_keeps_buttons_makes_icon_only_one_line_tooltip():
    bar = QuietBar()
    btn = QToolButton()
    btn.setText("Mark")
    out = bar.add_button(btn, "circle-check")
    bar.set_tooltip(btn, "Mark", "Space")
    assert out is btn and btn in bar.buttons()
    assert btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert not btn.icon().isNull()
    tip = btn.toolTip().replace(" ", " ")
    assert tip == "Mark  Space" and "\n" not in tip and " " not in btn.toolTip().strip()
    assert btn.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert bar.height() <= 40


def test_bar_overflow_menu_title_copy_slot():
    bar = QuietBar()
    act = bar.overflow_menu().addAction("Fit")
    assert act in bar.overflow_menu().actions()
    bar.set_title("Shoot", 12)
    assert "Shoot" in bar.title() and "12" in bar.title()
    copy = QToolButton()
    copy.setText("Copy")
    assert bar.set_copy_button(copy) is copy
    assert copy.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert bar.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_pill_state_texts_and_copy_signal():
    host = QWidget()
    host.resize(800, 600)
    pill = LightsOutPill(host)
    pill.set_state(True, "3 / 40")
    assert pill.marked_text() == "Marked" and pill.position_text() == "3 / 40"
    pill.set_state(False, "4 / 40")
    assert pill.marked_text() == "" and pill.position_text() == "4 / 40"
    got = []
    pill.copy_clicked.connect(lambda: got.append("copy"))
    pill.help_clicked.connect(lambda: got.append("help"))
    pill._copy.click()
    pill._help.click()
    assert got == ["copy", "help"]


def test_pill_no_focus_anywhere():
    pill = LightsOutPill(QWidget())
    assert pill.focusPolicy() == Qt.FocusPolicy.NoFocus
    for w in pill.findChildren(QWidget):
        assert w.focusPolicy() == Qt.FocusPolicy.NoFocus, w


def test_pill_recentres_on_parent_resize():
    host = QWidget()
    host.resize(800, 600)
    pill = LightsOutPill(host)
    host.show()  # a hidden widget gets its Resize event only when shown
    try:
        assert abs(pill.geometry().center().x() - 400) <= 1
        host.resize(1200, 900)
        assert abs(pill.geometry().center().x() - 600) <= 1
        assert 800 < pill.geometry().bottom() < 900
    finally:
        host.close()


def test_retheme_changes_colours(restore_dark):
    app = QApplication.instance()
    bar = QuietBar()
    pill = LightsOutPill(QWidget())
    btn = QToolButton()
    bar.add_button(btn, "copy")
    theme.apply(app, True)
    bar.retheme()
    pill.retheme()
    dark_css, dark_hud = bar.styleSheet(), pill.hud_color().name(QColor.NameFormat.HexArgb)
    dark_px = _opaque_colors(btn.icon().pixmap(18, 18))
    theme.apply(app, False)
    bar.retheme()
    pill.retheme()
    assert bar.styleSheet() != dark_css
    assert _opaque_colors(btn.icon().pixmap(18, 18)) != dark_px
    # hud differs only once the theme task adds it; never worse than equal
    assert pill.hud_color().name(QColor.NameFormat.HexArgb) != dark_hud or not hasattr(
        theme.current(), "hud")
