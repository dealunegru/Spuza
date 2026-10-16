"""The peaking overlay on the big picture (flag focus_peaking, advanced-only).

Contract for the window (wired by spz-nx-wire): build one `PeakingOverlay(viewer)`; call `set_on(...)` when P
toggles; call `show(index, qimg)` after each `viewer.show_image(index, ...)` with the cached preview. The mask is
computed on this object's own low-priority single-thread pool, never on the GUI thread and never ahead of decodes;
until it is ready the plain picture shows, and a step is never held back. The job is debounced by one
single-shot timer (SETTLE_MS): each show() restarts it, so a fast walk computes no masks (a 60-110 ms job per step
competed with the decode threads for CPU and the GIL); a cached mask is placed at once.

Masks are kept for at most KEEP (2) pictures: the current one and the one before it. The overlay is a child item of
the viewer's picture item, so it shares its transform; it is rescaled to the item's pixmap on every pixmap swap.
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, Qt, QThread, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem

from .. import features, peaking

KEEP = 2  # masks cached: current + previous picture
SETTLE_MS = 150  # a mask job starts only after the same picture has been on screen this long (a step never waits)


class _Job(QRunnable):
    def __init__(self, owner, token: int, index: int, qimg: QImage, threshold: int, half: bool):
        super().__init__()
        self.setAutoDelete(True)
        self._args = (owner, token, index, qimg, threshold, half)

    def run(self) -> None:
        owner, token, index, qimg, threshold, half = self._args
        try:
            out = peaking.mask_image(qimg, threshold, half)
        except Exception:  # noqa: BLE001 - a failed mask just leaves the plain picture
            out = None
        try:
            owner._result.emit(token, index, out)
        except RuntimeError:  # owner already deleted
            pass


class PeakingOverlay(QObject):
    ready = Signal(int)  # the mask for picture `index` is on screen
    _result = Signal(int, int, object)

    def __init__(self, viewer, threshold: int = 60, half: bool = True, parent=None):
        super().__init__(parent)
        self._viewer = viewer
        self._threshold = max(1, min(255, int(threshold)))
        self._half = half
        self._on = False
        self._token = 0  # bumped whenever pending results become stale
        self._index: int | None = None  # picture the viewer shows (while on)
        self._qimg: QImage | None = None  # its preview, for a threshold change
        self._shown: int | None = None  # index whose mask is on the item
        self._cache: OrderedDict[int, QImage] = OrderedDict()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._pool.setThreadPriority(QThread.Priority.LowPriority)
        self._settle = QTimer(self)  # the settle delay: fires _start_pending once the picture has stayed
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self._start_pending)
        self._item = QGraphicsPixmapItem(viewer.image_item())
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._item.setVisible(False)
        self._result.connect(self._on_result)
        viewer.pixmap_changed.connect(self._on_pixmap)

    def is_on(self) -> bool:
        return self._on

    def set_on(self, on: bool) -> None:
        """Show (and compute) masks, or hide the overlay and stop computing. Saving is the caller's business."""
        on = bool(on)
        if on == self._on or (on and not features.enabled("focus_peaking")):
            return
        self._on = on
        self._token += 1
        if on:
            return
        self._settle.stop()
        self._pool.clear()
        self._hide()
        self._cache.clear()
        self._index = self._qimg = None

    def set_threshold(self, n: int) -> None:
        """New threshold (1-255): cached masks are dropped and the current one recomputed when on."""
        n = max(1, min(255, int(n)))
        if n == self._threshold:
            return
        self._threshold = n
        self._token += 1
        self._pool.clear()
        self._cache.clear()
        self._hide()
        if self._on and self._index is not None:
            self._settle.start()

    def show(self, index: int, qimg: QImage | None) -> None:
        """The viewer now shows picture `index` from preview `qimg` (None: nothing to peak). No-op while off."""
        if not self._on:
            return
        self._token += 1
        self._pool.clear()
        self._settle.stop()
        self._index, self._qimg = index, qimg
        if qimg is None or qimg.isNull():
            self._hide()
            return
        hit = self._cache.get(index)
        if hit is not None:
            self._cache.move_to_end(index)
            self._place(index, hit)
            return
        self._hide()
        self._settle.start()  # restarts on every show(): the job starts only for a picture that stayed

    def mask_for(self, index: int) -> QImage | None:
        """The finished overlay image for `index`, if cached (for compare cells and tests)."""
        return self._cache.get(index)

    def shutdown(self, timeout_ms: int = 3000) -> None:
        self._on = False
        self._token += 1
        self._settle.stop()
        self._pool.clear()
        self._pool.waitForDone(timeout_ms)
        self._hide()

    # -- internals

    def _start_pending(self) -> None:
        if self._on and self._index is not None:
            self._submit(self._index, self._qimg)

    def _submit(self, index: int, qimg: QImage | None) -> None:
        if qimg is None or qimg.isNull():
            return
        self._pool.start(_Job(self, self._token, index, qimg, self._threshold, self._half), -1)

    def _on_result(self, token: int, index: int, img) -> None:
        if not self._on or token != self._token or index != self._index or img is None:
            return  # stale: the user moved on
        self._cache[index] = img
        while len(self._cache) > KEEP:
            self._cache.popitem(last=False)
        self._place(index, img)

    def _hide(self) -> None:
        self._shown = None
        self._item.setVisible(False)
        self._item.setPixmap(QPixmap())

    def _place(self, index: int, img: QImage) -> None:
        self._item.setPixmap(QPixmap.fromImage(img))
        self._shown = index
        if self._fit():
            self._item.setVisible(True)
            self.ready.emit(index)

    def _fit(self) -> bool:
        """Scale the mask to cover the item's current pixmap; False when there is nothing to cover."""
        pm = self._viewer.image_item().pixmap()
        mine = self._item.pixmap()
        if pm.isNull() or mine.isNull():
            return False
        self._item.setTransform(QTransform.fromScale(pm.width() / mine.width(), pm.height() / mine.height()))
        return True

    def _on_pixmap(self, index: int) -> None:
        if self._shown is None:
            return
        if index != self._shown or not self._fit():
            self._hide()  # the picture changed under the mask
