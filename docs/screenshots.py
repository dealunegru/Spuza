"""Headless screenshots of the real Spuza window for the README.

    QT_QPA_PLATFORM=offscreen uv run python docs/screenshots.py docs/samples docs

The offscreen platform has no fonts of its own; on Windows point QT_QPA_FONTDIR at a folder
holding the UI font (the README shots use Segoe UI). Writes screenshot-cull.png,
screenshot-fullscreen.png and screenshot-review.png into the output folder.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from spuza.catalog import Catalog, Prefetcher, scan
from spuza.ui import theme
from spuza.ui.window import MainWindow

SIZE = (1440, 900)


def main(argv: list[str]) -> int:
    root, out = Path(argv[1]), Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setApplicationName("Spuza")
    catalog = Catalog(scan(root))
    window = MainWindow(catalog, Prefetcher(catalog), root)
    theme.apply(app, True)
    window.resize(*SIZE)
    window.show()
    viewer = window.viewer

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
            time.sleep(0.005)

    def wait_for(i: int, timeout: float = 20.0) -> None:
        end = time.monotonic() + timeout
        while not (viewer.index == i and viewer.has_image):
            if time.monotonic() > end:
                raise SystemExit(f"picture {i} did not appear in {timeout:.0f} s")
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
            time.sleep(0.005)

    # Mark 1, 3 and 4, then stand on the first picture for the hero shot.
    wait_for(0)
    window.toggle_mark()
    for i, mark in ((1, False), (2, True), (3, True)):
        window.step(1)
        wait_for(i)
        if mark:
            window.toggle_mark()
    window.go_to(0)
    wait_for(0)
    pump(0.6)
    window.grab().save(str(out / "screenshot-cull.png"))

    window.set_bleed(True)
    pump(0.3)
    window.resize(*SIZE)
    pump(0.6)
    window.grab().save(str(out / "screenshot-fullscreen.png"))
    window.set_bleed(False)
    pump(0.3)

    window.open_review()
    pump(2.5)
    window.grab().save(str(out / "screenshot-review.png"))
    print(f"3 screenshots in {out} ({catalog.marked_count} marked of {len(catalog)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
