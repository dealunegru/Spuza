"""docs/img is exactly what docs/shots.py says it writes, and small enough to ship. No display needed."""

from __future__ import annotations

import ast
from pathlib import Path

from PIL import Image

DOCS = Path(__file__).resolve().parent.parent / "docs"
IMG = DOCS / "img"
PNG_CAP = 600 * 1024
GIF_CAP = 3 * 1024 * 1024


def shots() -> tuple[str, ...]:
    tree = ast.parse((DOCS / "shots.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SHOTS" for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("docs/shots.py has no module-level SHOTS")


def test_images_equal_shots():
    assert IMG.is_dir(), "docs/img is missing: run docs/shots.py"
    assert sorted(p.name for p in IMG.iterdir() if p.is_file()) == sorted(shots())


def test_pngs_same_width_and_small():
    widths = set()
    for name in shots():
        if name.endswith(".png"):
            p = IMG / name
            assert p.stat().st_size < PNG_CAP, name
            with Image.open(p) as im:
                widths.add(im.width)
    assert len(widths) == 1


def test_hero_is_a_small_animation():
    p = IMG / "hero.gif"
    assert p.stat().st_size < GIF_CAP
    with Image.open(p) as im:
        assert im.n_frames > 1
