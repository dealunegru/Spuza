"""The icon pipeline: committed .ico/.icns match a fresh render, the runtime PNG ships, specs and window use it."""

import importlib.util
import re
import sys
from importlib import resources
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "packaging" / "icon"
ICO_SIZES = {16, 24, 32, 48, 64, 128, 256}
ICNS_PIXELS = {32, 64, 128, 256, 512, 1024}  # Pillow writes ic11..ic14 etc: 16 px lives as the 32 px @2x


def _load_build_icon():
    spec = importlib.util.spec_from_file_location("build_icon", ICON_DIR / "build_icon.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_icon"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ico_sizes(path: Path) -> set[int]:
    with Image.open(path) as im:
        return {w for w, h in im.info["sizes"] if w == h}


def _icns_pixels(path: Path) -> set[int]:
    with Image.open(path) as im:
        return {w * scale for w, h, scale in im.info["sizes"]}


def test_committed_ico_has_exactly_the_windows_sizes():
    assert _ico_sizes(ICON_DIR / "spuza.ico") == ICO_SIZES


def test_committed_icns_has_the_mac_range():
    pixels = _icns_pixels(ICON_DIR / "spuza.icns")
    assert ICNS_PIXELS <= pixels
    assert max(pixels) == 1024


def test_rebuild_matches_committed_sizes(tmp_path):
    made = _load_build_icon().build(tmp_path)
    assert _ico_sizes(made["ico"]) == _ico_sizes(ICON_DIR / "spuza.ico")
    assert _icns_pixels(made["icns"]) == _icns_pixels(ICON_DIR / "spuza.icns")
    with Image.open(made["png"]) as new, Image.open(resources.files("spuza") / "assets" / "spuza.png") as old:
        assert new.size == old.size == (256, 256)


def test_sources_by_platform_with_fallback(tmp_path):
    mod = _load_build_icon()
    assert mod.source_for("icns", 16) == mod.SVG  # the Mac tile carries its own shadow: never the win variant
    assert mod.source_for("ico", 16) == mod.SMALL_SVG
    assert mod.source_for("ico", 24) == mod.WIN_SVG
    assert mod.source_for("png", 256) == mod.WIN_SVG
    lone = tmp_path / "spuza.svg"
    lone.write_bytes(mod.SVG.read_bytes())
    for target, size in (("ico", 16), ("ico", 256), ("png", 256), ("icns", 1024)):
        assert mod.source_for(target, size, lone, tmp_path / "none-win.svg", tmp_path / "none-16.svg") == lone


def test_committed_variants_exist():
    for name in ("spuza.svg", "spuza-win.svg", "spuza-16.svg"):
        assert (ICON_DIR / name).stat().st_size > 500


def test_packaged_png_is_reachable_through_the_package():
    png = resources.files("spuza") / "assets" / "spuza.png"
    assert png.is_file()
    with Image.open(png) as im:
        assert im.size == (256, 256)


def _spec(name: str) -> str:
    return (ROOT / "packaging" / name).read_text(encoding="utf-8")


def test_windows_spec_sets_the_ico_on_both_exes():
    text = _spec("spuza-windows.spec")
    assert len(re.findall(r'icon=str\(HERE / "icon" / "spuza\.ico"\)', text)) == 2
    assert text.count("EXE(") == 2
    assert 'src" / "spuza" / "assets"' in text  # the PNG is collected as data for the frozen app


def test_mac_spec_sets_the_icns_on_the_bundle():
    text = _spec("spuza.spec")
    bundle = text[text.index("BUNDLE(") :]
    assert 'icon=str(HERE / "icon" / "spuza.icns")' in bundle
    assert 'src" / "spuza" / "assets"' in text


def test_window_icon_is_set_by_app_setup():
    from spuza import cli

    app = QApplication.instance()
    app.setWindowIcon(QIcon())
    assert app.windowIcon().isNull()
    assert cli._app() is app
    assert not app.windowIcon().isNull()


def test_missing_icon_file_is_skipped(monkeypatch):
    from spuza import cli

    def gone(_traversable):
        raise FileNotFoundError("spuza.png")

    monkeypatch.setattr(resources, "as_file", gone)
    app = QApplication.instance()
    app.setWindowIcon(QIcon())
    assert cli._set_window_icon(app) is False
    assert app.windowIcon().isNull()


@pytest.mark.parametrize("name", ["spuza.ico", "spuza.icns"])
def test_committed_icons_are_not_empty(name):
    assert (ICON_DIR / name).stat().st_size > 1000
