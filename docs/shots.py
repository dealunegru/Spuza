"""Regenerate every README image from the real Spuza window.

    uv run python docs/shots.py [SOURCE] [OUT]        (defaults: docs/samples, docs/img)

Runs on the real platform (windows / cocoa), not offscreen: offscreen on Windows draws every glyph as a
box. Preferences and resume sessions go to a temp folder, so the script never touches yours.
Writes the files named in SHOTS.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

SHOTS = ("hero.gif", "workbench.png", "bleed.png", "review.png", "copy.png", "prefs.png",
         "compare.png", "peaking.png")

SIZE = (1440, 900)  # the window, for every shot
OUT_WIDTH = 1200  # every image is scaled to this width
TIMEOUT = 30.0  # seconds any single wait may take
HERE = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        print("shots.py: QT_QPA_PLATFORM=offscreen renders every glyph as a box on Windows; "
              "unset it and run on the real platform.", file=sys.stderr)
        return 2
    source = Path(argv[1]) if len(argv) > 1 else HERE / "samples"
    out = Path(argv[2]) if len(argv) > 2 else HERE / "img"
    if not source.is_dir():
        print(f"shots.py: source folder {source} does not exist", file=sys.stderr)
        return 2
    scratch = Path(tempfile.mkdtemp(prefix="spuza-shots-"))
    os.environ["SPUZA_SETTINGS_FILE"] = str(scratch / "prefs.ini")  # before any spuza import reads settings
    os.environ["SPUZA_SESSIONS_DIR"] = str(scratch / "sessions")
    try:
        return _run(source, out)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _run(root: Path, out: Path) -> int:
    from PIL import Image
    from PySide6.QtCore import QBuffer, QEventLoop, QIODevice
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from spuza import features
    from spuza.catalog import Catalog, Prefetcher, scan
    from spuza.ui import gcguard, theme
    from spuza.ui import window as win_mod
    from spuza.ui.window import MainWindow

    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():  # only our own kinds of file
        if old.suffix.lower() in (".png", ".gif"):
            old.unlink()
    app = QApplication([])
    app.setApplicationName("Spuza")
    gcguard.install(app)
    features.set_mode(features.CLASSIC)
    catalog = Catalog(scan(root))
    if len(catalog) < 4:
        raise SystemExit(f"need at least 4 pictures in {root}, found {len(catalog)}")
    prefetcher = Prefetcher(catalog)
    window = MainWindow(catalog, prefetcher, root)
    theme.apply(app, True)
    window.resize(*SIZE)
    window.show()
    viewer = window.viewer
    # Show neutral folders, never this machine's paths.
    window.sidebar.set_root(Path("D:/Photos/Card"))
    keepers = Path(Path.home().anchor) / "Photos" / "Keepers"  # a drive that exists, so free space shows
    win_mod.default_copy_dest = lambda: keepers

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
            time.sleep(0.005)

    def wait_until(cond, what: str, timeout: float = TIMEOUT) -> None:
        end = time.monotonic() + timeout
        while not cond():
            if time.monotonic() > end:
                raise SystemExit(f"timed out after {timeout:.0f} s waiting for {what}")
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
            time.sleep(0.005)

    def wait_picture(i: int) -> None:
        wait_until(lambda: viewer.index == i and viewer.has_image, f"picture {i}")

    def to_pil(img: QImage) -> Image.Image:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        pil = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")
        h = round(pil.height * OUT_WIDTH / pil.width)
        return pil.resize((OUT_WIDTH, h), Image.Resampling.LANCZOS)

    def grab() -> Image.Image:
        return to_pil(window.grab().toImage())

    def with_sheet(sheet) -> Image.Image:
        """The window dimmed, the sheet (a separate top-level dialog) centred over it."""
        base = window.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
        p = QPainter(base)
        p.fillRect(base.rect(), QColor(0, 0, 0, 140))
        s = sheet.grab().toImage()
        p.drawImage((base.width() - s.width()) // 2, (base.height() - s.height()) // 2, s)
        p.end()
        return to_pil(base)

    def save_png(name: str, im: Image.Image) -> None:
        im.save(out / name, optimize=True)

    # -- the card: step through, marking 1, 3 and 4 ---------------------------------------------
    frames: list[tuple[Image.Image, int]] = []
    wait_picture(0)
    pump(0.6)
    frames.append((grab(), 800))
    window.toggle_mark()
    pump(0.4)
    frames.append((grab(), 800))
    for i, mark in ((1, False), (2, True), (3, True)):
        window.step(1)
        wait_picture(i)
        pump(0.4)
        frames.append((grab(), 700))
        if mark:
            window.toggle_mark()
            pump(0.4)
            frames.append((grab(), 800))
    window.go_to(0)
    wait_picture(0)
    pump(0.6)
    work = grab()
    frames.append((work, 1800))
    if catalog.marked_count != 3:
        raise SystemExit(f"expected 3 marked, got {catalog.marked_count}")
    save_png("workbench.png", work)
    pal = [f.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f, _ in frames]
    pal[0].save(out / "hero.gif", save_all=True, append_images=pal[1:], duration=[d for _, d in frames],
                loop=0, optimize=True, disposal=1)

    window.set_bleed(True)
    pump(0.3)
    window.resize(*SIZE)
    pump(0.6)
    save_png("bleed.png", grab())
    window.set_bleed(False)
    pump(0.3)

    window.open_review()
    pump(2.5)
    save_png("review.png", grab())
    window.stack.setCurrentIndex(0)  # back to the viewer for what follows
    pump(0.3)

    window.open_copy()
    wait_until(lambda: window._sheet is not None and window._sheet.isVisible(), "the copy sheet")
    pump(0.5)
    save_png("copy.png", with_sheet(window._sheet))
    window._sheet.reject()
    pump(0.3)

    # -- advanced mode ----------------------------------------------------------------------------
    window.set_advanced(True)
    pump(0.3)
    window.open_prefs()
    wait_until(lambda: window._prefs_sheet is not None and window._prefs_sheet.isVisible(), "preferences")
    pump(0.5)
    save_png("prefs.png", with_sheet(window._prefs_sheet))
    window._prefs_sheet.reject()
    pump(0.3)

    window.go_to(0)
    wait_picture(0)
    window.open_compare()
    cv = window.compare
    if cv is None or len(cv.indices()) < 2:
        raise SystemExit("compare view did not open with two pictures")
    wait_until(lambda: all(cv.cell(i) is not None and cv.cell(i).has_image for i in cv.indices()),
               "the compare cells' pictures")
    pump(1.0)
    save_png("compare.png", grab())
    cv.close_compare()
    pump(0.5)

    window.go_to(1)
    wait_picture(1)
    window.set_peaking(True)
    if window.peaking is None:
        raise SystemExit("peaking overlay was not built")
    wait_until(lambda: window.peaking.mask_for(1) is not None, "the peaking mask")
    pump(1.0)
    save_png("peaking.png", grab())

    features.set_mode(features.CLASSIC)
    if window.compare is not None:
        window.compare.shutdown()
    prefetcher.shutdown()
    window.close()
    for name in SHOTS:
        print(f"{name:14} {(out / name).stat().st_size / 1024:8.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
