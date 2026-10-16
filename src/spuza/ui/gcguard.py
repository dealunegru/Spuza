"""Run Python's cyclic garbage collector on the GUI thread only.

CPython's automatic GC runs on whichever thread happens to allocate. Spuza's QThreadPool workers run
Python (PIL decode), so a worker can collect a reference cycle holding a QObject, and PySide then
destroys that QObject on the worker thread (PYSIDE-810): a crash or segfault. The standard mitigation
is to switch automatic collection off and collect from a timer on the GUI thread.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer

# Cycles are not urgent; 500 ms keeps memory bounded while costing one cheap gc.get_count() per tick.
INTERVAL_MS = 500

_guard: GuiThreadGC | None = None


class GuiThreadGC(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self.collect)
        self._timer.start()

    def collect(self) -> None:
        """Collect the oldest generation whose count passed its threshold (what automatic GC would do)."""
        counts = gc.get_count()
        thresholds = gc.get_threshold()
        for gen in range(len(counts) - 1, -1, -1):
            if counts[gen] > thresholds[gen]:
                gc.collect(gen)
                break


def install(app: QObject) -> GuiThreadGC:
    """Disable automatic GC and start the GUI-thread collector. Idempotent."""
    global _guard
    if _guard is None:
        gc.disable()
        _guard = GuiThreadGC(app)
    return _guard
