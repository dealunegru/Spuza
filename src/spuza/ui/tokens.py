"""Spuza's design tokens (L1): the values the theme engine turns into a palette and one stylesheet.

Mirrors state/research/spuza-design/tokens.json in MrAgent. Every grey is R=G=B; colour is for state
and focus only. Nothing else in the package spells a colour: ask `theme.current()`.
"""

from __future__ import annotations

DARK = {
    "stage": "#1c1c1c", "window": "#232323", "panel": "#2a2a2a", "raised": "#333333",
    "hover": "#3a3a3a", "pressed": "#444444", "border": "#383838",
    "text": "#e6e6e6", "text_dim": "#9e9e9e", "text_disabled": "#5e5e5e",
    "focus": "#5a9bff", "on_focus": "#ffffff",
    "marked": "#34c759", "rejected": "#ff5a4f", "cropped": "#b38cff", "warning": "#ffb340",
    "hud": "rgba(16,16,16,200)", "key_chip_bg": "#3a3a3a", "key_chip_text": "#d0d0d0",
}

LIGHT = {
    "stage": "#7f7f7f", "window": "#ececec", "panel": "#f6f6f6", "raised": "#ffffff",
    "hover": "#e2e2e2", "pressed": "#d6d6d6", "border": "#cfcfcf",
    "text": "#1d1d1d", "text_dim": "#6b6b6b", "text_disabled": "#a8a8a8",
    "focus": "#0a6cff", "on_focus": "#ffffff",
    "marked": "#1f9d40", "rejected": "#d70015", "cropped": "#8944ab", "warning": "#b36b00",
    "hud": "rgba(24,24,24,190)", "key_chip_bg": "#e2e2e2", "key_chip_text": "#3a3a3a",
}

TYPE = {"caption": 11, "body": 13, "title": 15, "display": 20, "weight_strong": 600}  # px
SPACE = (4, 8, 12, 16, 24)
RADIUS = {"small": 4, "control": 6, "sheet": 10}
ICON = {"set": "lucide (ISC)", "size": 18, "stroke": 1.75}

tokens = {"dark": DARK, "light": LIGHT, "type": TYPE, "space": SPACE, "radius": RADIUS, "icon": ICON}
