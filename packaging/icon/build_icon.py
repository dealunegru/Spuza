"""Render packaging/icon/spuza*.svg into the committed icon files.

    uv run python packaging/icon/build_icon.py [--out DIR]

Writes spuza.ico (Windows), spuza.icns (macOS) and spuza.png (runtime window icon) into DIR (default:
packaging/icon for the ico/icns, src/spuza/assets for the png copy). Sources, by platform convention:
spuza.svg is the macOS tile (squircle, with its shadow) and feeds the .icns; spuza-win.svg, when present,
is the flat Windows tile and feeds the .ico and the runtime png; spuza-16.svg, when present, is the
hand-made small variant used for the 16 px .ico entry. A missing variant falls back to spuza.svg, so a
single plain SVG still builds everything. The outputs are committed, so CI and PyInstaller never need
the renderer.

Renderer: resvg-py (dev dependency group). It is pure Rust with abi3 wheels for windows-latest
(win_amd64), macos-15 (macosx_11_0_arm64) and ubuntu-latest (manylinux x86_64), per uv.lock; cairosvg
needs a native cairo that those runners lack. Every size is rendered straight from the SVG at that size
(no downscaling of one big bitmap), then handed to Pillow's ICO and ICNS writers via append_images.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SVG = HERE / "spuza.svg"
WIN_SVG = HERE / "spuza-win.svg"
SMALL_SVG = HERE / "spuza-16.svg"
SMALL_MAX = 16  # .ico entries up to this size come from the hand-made small variant
ASSETS = HERE.parent.parent / "src" / "spuza" / "assets"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)
RUNTIME_PNG_SIZE = 256


def render(svg: Path, size: int) -> Image.Image:
    """The SVG rendered at size x size, RGBA."""
    import resvg_py

    png = resvg_py.svg_to_bytes(svg_path=str(svg), width=size, height=size)
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def _save_multi(path: Path, fmt: str, images: dict[int, Image.Image]) -> None:
    sizes = sorted(images)
    base = images[sizes[-1]]
    rest = [images[s] for s in sizes[:-1]]
    kwargs = {"sizes": [(s, s) for s in sizes]} if fmt == "ICO" else {}
    base.save(path, format=fmt, append_images=rest, **kwargs)


def source_for(target: str, size: int, svg: Path = SVG, win: Path = WIN_SVG, small: Path = SMALL_SVG) -> Path:
    """Which SVG renders `target` ("ico", "icns" or "png") at `size`: the Windows and small variants when
    they exist, else the one macOS SVG."""
    if target == "icns":
        return svg
    if size <= SMALL_MAX and small.is_file():
        return small
    return win if win.is_file() else svg


def build(out: Path | None = None, svg: Path = SVG, win: Path = WIN_SVG, small: Path = SMALL_SVG) -> dict[str, Path]:
    """Write the icon files; returns name -> path. With `out`, everything goes into that one folder."""
    ico_dir = out or HERE
    asset_dir = out or ASSETS
    ico_dir.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)
    cache: dict[tuple[Path, int], Image.Image] = {}

    def at(target: str, size: int) -> Image.Image:
        src = source_for(target, size, svg, win, small)
        if (src, size) not in cache:
            cache[(src, size)] = render(src, size)
        return cache[(src, size)]

    made = {
        "ico": ico_dir / "spuza.ico",
        "icns": ico_dir / "spuza.icns",
        "png": asset_dir / "spuza.png",
    }
    _save_multi(made["ico"], "ICO", {s: at("ico", s) for s in ICO_SIZES})
    _save_multi(made["icns"], "ICNS", {s: at("icns", s) for s in ICNS_SIZES})
    at("png", RUNTIME_PNG_SIZE).save(made["png"], format="PNG", optimize=True)
    return made


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, help="write everything into this folder instead of the repo")
    args = ap.parse_args(argv)
    for name, path in build(args.out).items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
