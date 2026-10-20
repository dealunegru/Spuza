"""Bundled Lucide icons (ISC, see assets/icons/LICENSE), tinted per theme.

The SVGs use ``stroke="currentColor"``; we substitute the stroke colour and render with QSvgRenderer
at the device pixel ratio. Read through importlib.resources so it works from a wheel and PyInstaller.
"""

from __future__ import annotations

import re
from importlib import resources

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# tokens.json dark values, used when theme.Colors predates the new fields.
_FALLBACK = {"text": "#e6e6e6", "text_disabled": "#5e5e5e"}

_cache: dict[tuple, QPixmap] = {}
_svg_cache: dict[str, str] = {}


def _dir():
    return resources.files("spuza").joinpath("assets", "icons")


def names() -> list[str]:
    """Bundled icon names (file stems), sorted."""
    return sorted(p.name[:-4] for p in _dir().iterdir() if p.name.endswith(".svg"))


def parse_color(value) -> QColor:
    """QColor from '#rrggbb', a Qt colour name, or CSS 'rgba(r,g,b,a)' (a 0-255, or 0-1 with a dot)."""
    if isinstance(value, QColor):
        return QColor(value)
    s = str(value).strip()
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", s)
    if m:
        r, g, b = (min(255, int(m.group(i))) for i in (1, 2, 3))
        a = 255
        if m.group(4) is not None:
            f = float(m.group(4))
            a = round(f * 255) if f <= 1 and "." in m.group(4) else min(255, round(f))
        return QColor(r, g, b, a)
    col = QColor(s)
    return col if col.isValid() else QColor("#808080")


def _source(name: str) -> str:
    if name not in _svg_cache:
        try:
            _svg_cache[name] = _dir().joinpath(f"{name}.svg").read_text(encoding="utf-8")
        except OSError:
            raise KeyError(f"no bundled icon {name!r}") from None
    return _svg_cache[name]


def _default_dpr() -> float:
    app = QGuiApplication.instance()
    scr = app.primaryScreen() if app is not None else None
    return float(scr.devicePixelRatio()) if scr is not None else 1.0


def pixmap(name: str, color, size: int = 18, dpr: float | None = None) -> QPixmap:
    """The icon as a QPixmap of size*dpr device pixels with its stroke set to ``color``."""
    dpr = float(dpr) if dpr else _default_dpr()
    col = parse_color(color)
    key = (name, col.name(QColor.NameFormat.HexArgb), int(size), dpr)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    svg = _source(name).replace("currentColor", col.name())
    if col.alpha() != 255:
        svg = svg.replace("<svg", f'<svg stroke-opacity="{col.alphaF():.3f}"', 1)
    side = max(1, round(size * dpr))
    img = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(svg.encode("utf-8")).render(p, QRectF(0, 0, side, side))
    p.end()
    img.setDevicePixelRatio(dpr)
    pm = QPixmap.fromImage(img)
    _cache[key] = pm
    return pm


def icon(name: str, color, size: int = 18, dpr: float | None = None) -> QIcon:
    """A QIcon (Normal only) of the named icon in one colour."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size, dpr), QIcon.Mode.Normal)
    return ic


def themed_icon(name: str, c, size: int = 18, dpr: float | None = None) -> QIcon:
    """Normal = c.text, Disabled = c.text_disabled (not Qt's automatic greying)."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, getattr(c, "text", _FALLBACK["text"]), size, dpr), QIcon.Mode.Normal)
    off = getattr(c, "text_disabled", _FALLBACK["text_disabled"])
    ic.addPixmap(pixmap(name, off, size, dpr), QIcon.Mode.Disabled)
    return ic


def clear_cache() -> None:
    _cache.clear()
