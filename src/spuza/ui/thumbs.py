"""The filmstrip's own thumbnail loader: far cells fill in on scroll, without a click.

The main prefetcher only holds the window around the current picture, so cells far along the strip stayed
empty until the user clicked one. This loader decodes the strip's visible range (plus a screen either side)
from each file's embedded preview at thumbnail size, on its own one-thread low-priority pool, so the main
picture's decode is never queued behind it. Requests are bounded (MAX_INFLIGHT submitted at a time), a new
request replaces the old one (stale work is skipped before it decodes), and finished thumbnails live in an
LRU of THUMB_CACHE_MAX images.
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal
from PySide6.QtGui import QImage

THUMB_CACHE_MAX = 400  # decoded thumbnails kept; least recently used dropped first
MAX_INFLIGHT = 4  # tasks submitted to the pool at once; the rest wait here and can still be dropped
DECODE_SIDE = 320  # long side asked of the JPEG draft decoder before the final downscale


def _decode_thumb(path, w: int, h: int) -> QImage | None:
    """Embedded preview (or the file itself) decoded at thumbnail size; None when it cannot be read."""
    from PIL import Image

    from spuza import preview

    try:
        im, _info = preview.load(path, max_side=max(DECODE_SIDE, w, h))
    except Exception:
        return None
    try:
        im.thumbnail((w, h), Image.Resampling.BILINEAR, reducing_gap=2.0)
        im = im.convert("RGB")
        return QImage(im.tobytes(), im.width, im.height, im.width * 3, QImage.Format.Format_RGB888).copy()
    except Exception:
        return None


class _Task(QRunnable):
    def __init__(self, loader: ThumbLoader, i: int, path, w: int, h: int):
        super().__init__()
        self.loader, self.i, self.path, self.w, self.h = loader, i, path, w, h

    def run(self) -> None:
        loader, i = self.loader, self.i
        if loader._closed or i not in loader._wanted:  # stale: the user scrolled on
            loader.done.emit(i, QImage(), False)
            return
        img = _decode_thumb(self.path, self.w, self.h)
        loader.done.emit(i, img if img is not None else QImage(), img is None)


class ThumbLoader(QObject):
    """Asks for thumbnails by index; `loaded(i)` fires on the UI thread when one is ready to read."""

    done = Signal(int, QImage, bool)  # worker -> UI: (index, image or null, failed)
    loaded = Signal(int)

    def __init__(self, paths_of, parent=None):
        """paths_of(i) -> the file to decode for picture i."""
        super().__init__(parent)
        self._paths_of = paths_of
        self._cache: OrderedDict[int, QImage] = OrderedDict()
        self._failed: set[int] = set()
        self._wanted: frozenset[int] = frozenset()  # replaced whole, never mutated: workers read it unlocked
        self._queue: list[int] = []
        self._inflight = 0
        self._size = (0, 0)
        self._closed = False
        self._pool: QThreadPool | None = None
        self.done.connect(self._on_done)

    def cached(self, i: int) -> QImage | None:
        img = self._cache.get(i)
        if img is not None:
            self._cache.move_to_end(i)
        return img

    def request(self, indices, w: int, h: int) -> None:
        """Want these indices (nearest the front first) at w x h; replaces any earlier request."""
        if self._closed:
            return
        if (w, h) != self._size:  # a new device pixel ratio: old images are the wrong size
            self._cache.clear()
            self._failed.clear()
            self._size = (w, h)
        order = [int(i) for i in indices]
        self._wanted = frozenset(order)
        self._queue = [i for i in order if i not in self._cache and i not in self._failed]
        for i in order:  # keep what is on screen warm in the LRU
            if i in self._cache:
                self._cache.move_to_end(i)
        self._pump()

    def _ensure_pool(self) -> QThreadPool:
        if self._pool is None:
            pool = QThreadPool(self)
            pool.setMaxThreadCount(1)
            try:
                pool.setThreadPriority(QThread.Priority.LowPriority)
            except Exception:  # older Qt: priority is a nicety
                pass
            self._pool = pool
        return self._pool

    def _pump(self) -> None:
        while self._queue and self._inflight < MAX_INFLIGHT and not self._closed:
            i = self._queue.pop(0)
            try:
                path = self._paths_of(i)
            except Exception:
                self._failed.add(i)
                continue
            self._inflight += 1
            self._ensure_pool().start(_Task(self, i, path, *self._size), -1)

    def _on_done(self, i: int, img: QImage, failed: bool) -> None:
        self._inflight = max(0, self._inflight - 1)
        if self._closed:
            return
        if failed:
            self._failed.add(i)
        elif not img.isNull():
            self._cache[i] = img
            self._cache.move_to_end(i)
            while len(self._cache) > THUMB_CACHE_MAX:
                self._cache.popitem(last=False)
            self.loaded.emit(i)
        self._pump()

    def shutdown(self, timeout_ms: int = 3000) -> None:
        """Drop what is queued and wait at most timeout_ms for a decode already running."""
        self._closed = True
        self._queue = []
        self._wanted = frozenset()
        if self._pool is not None:
            self._pool.clear()
            self._pool.waitForDone(max(0, int(timeout_ms)))
