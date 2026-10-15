"""Copy sheet: shows what will be copied, where, and whether it fits; then copies on a worker.

Closing mid-copy: the sheet ASKS first ("Stop copying and close?"). Yes cancels the copy (the half
written file is removed by ``run_copy``, finished files stay) and closes; No keeps copying.
``confirm_close`` and ``choose_folder`` are attributes so tests can inject them.

``progress(done, total)`` is in KiB (rounded up) so it fits a Qt int signal for any card size.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from spuza import copy as copy_mod
from spuza.copy import human_size

MIN_PROGRESS_INTERVAL = 1 / 30  # seconds between progress emits (about 30 per second)
BAR_STEPS = 1000
AMBER = "#d98a00"


def _kib(n: int) -> int:
    return (n + 1023) >> 10


def _show_name() -> str:
    import sys

    return "Show in Finder" if sys.platform == "darwin" else "Show in Explorer"


class _Signals(QObject):
    progress = Signal(int, int)
    done = Signal(object)


class _CopyJob(QRunnable):
    def __init__(self, plan, cancel: threading.Event, sig: _Signals, clock: Callable[[], float]):
        super().__init__()
        self.plan, self.cancel, self.sig, self.clock = plan, cancel, sig, clock
        self.setAutoDelete(False)  # the sheet keeps it alive until the signals are delivered
        self._last = float("-inf")

    def _on_progress(self, done: int, total: int, _src) -> None:
        now = self.clock()
        if done >= total or now - self._last >= MIN_PROGRESS_INTERVAL:
            self._last = now
            self.sig.progress.emit(_kib(done), _kib(total))

    def run(self) -> None:
        try:
            res = copy_mod.run_copy(self.plan, self._on_progress, self.cancel.is_set)
        except Exception as exc:  # noqa: BLE001 - surface as a failed result, never kill the thread
            res = copy_mod.CopyResult(failed=[(self.plan.dest, f"{type(exc).__name__}: {exc}")])
        self.sig.done.emit(res)


class CopySheet(QDialog):
    started = Signal()
    progress = Signal(int, int)
    finished = Signal(object)  # shadows QDialog.finished(int) on purpose (contract); see done()

    def __init__(self, pictures, default_dest=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Copy marked pictures")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(560, 520)
        self.pictures = list(pictures)
        self.dest: Path | None = Path(default_dest) if default_dest else None
        self.choose_folder: Callable = lambda parent, title, start: QFileDialog.getExistingDirectory(
            parent, title, start
        )
        self.free_space_fn: Callable = copy_mod.free_space
        self.open_url: Callable = QDesktopServices.openUrl
        self.confirm_close: Callable[[], bool] = self._ask_close
        self.clock: Callable[[], float] = time.monotonic
        self.plan = None
        self.result = None
        self.running = False
        self._cancel = threading.Event()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)
        self._sig = _Signals()
        self._sig.progress.connect(self._on_progress)
        self._sig.done.connect(self._on_done)
        self._job = None

        self.total_files = sum(len(p.files) for p in self.pictures)
        self.total_bytes = sum(p.size_bytes for p in self.pictures)

        self.title_label = QLabel(f"Copy {len(self.pictures)} marked picture{'s' if len(self.pictures) != 1 else ''}")
        f = self.title_label.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 2)
        self.title_label.setFont(f)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for p in self.pictures:
            self.list.addItem(f"{p.stem} · {p.kind} · {human_size(p.size_bytes)}")
        self.total_label = QLabel(
            f"{len(self.pictures)} pictures · {self.total_files} files · {human_size(self.total_bytes)}"
        )
        self.dest_label = QLabel()
        self.dest_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.dest_label.setWordWrap(True)
        self.choose_button = QPushButton("Choose folder…")
        self.choose_button.clicked.connect(self.pick_folder)
        self.free_label = QLabel()
        self.free_label.setWordWrap(True)
        self.plan_label = QLabel()
        self.bar = QProgressBar()
        self.bar.setRange(0, BAR_STEPS)
        self.bar.hide()
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.log_label = QLabel()
        self.log_label.setWordWrap(True)
        self.log_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setDefault(True)
        self.copy_button.clicked.connect(self.start_copy)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.show_button = QPushButton(_show_name())
        self.show_button.clicked.connect(self.show_folder)
        self.show_button.hide()
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        self.close_button.hide()

        where = QHBoxLayout()
        where.addWidget(QLabel("Where:"))
        where.addWidget(self.dest_label, 1)
        where.addWidget(self.choose_button)
        buttons = QHBoxLayout()
        buttons.addWidget(self.show_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.copy_button)
        lay = QVBoxLayout(self)
        lay.addWidget(self.title_label)
        lay.addLayout(where)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.total_label)
        lay.addWidget(self.free_label)
        lay.addWidget(self.plan_label)
        lay.addWidget(self.bar)
        lay.addWidget(self.result_label)
        lay.addWidget(self.log_label)
        lay.addLayout(buttons)
        self._update_dest()

    # ---- destination ---------------------------------------------------------------------------
    def pick_folder(self) -> None:
        start = str(self.dest) if self.dest else ""
        chosen = self.choose_folder(self, "Choose a folder for the copies", start)
        if chosen:
            self.dest = Path(chosen)
            self._update_dest()

    def _free(self) -> int | None:
        if self.dest is None:
            return None
        try:
            return int(self.free_space_fn(self.dest))
        except OSError:
            return None

    def space_short(self, needed: int | None = None) -> bool:
        free = self._free()
        return free is not None and (self.total_bytes if needed is None else needed) > free

    def _update_dest(self) -> None:
        self.plan = None
        self.plan_label.clear()
        if self.dest is None:
            self.dest_label.setText("No folder chosen")
            self.free_label.setText("")
            self.copy_button.setEnabled(False)
            return
        self.dest_label.setText(str(self.dest))
        free = self._free()
        if free is None:
            self.free_label.setText("Free space unknown")
            self.free_label.setStyleSheet("")
        elif self.space_short():
            self.free_label.setText(
                f"Not enough space: need {human_size(self.total_bytes)}, {human_size(free)} free here"
            )
            self.free_label.setStyleSheet(f"color: {AMBER}; font-weight: bold;")
        else:
            self.free_label.setText(f"{human_size(free)} free at the destination")
            self.free_label.setStyleSheet("")
        self.copy_button.setEnabled(bool(self.pictures) and not self.running and not self.space_short())
        if self.pictures and not self.space_short():
            self._show_plan()

    def _show_plan(self) -> None:
        self.plan = copy_mod.plan_copy([p.files for p in self.pictures], self.dest)
        skip = sum(1 for it in self.plan.items if it.action == "skip")
        self.plan_label.setText(f"Will copy {len(self.plan.items) - skip} files, skip {skip} already there")

    # ---- copying -------------------------------------------------------------------------------
    def start_copy(self) -> None:
        if self.running or self.dest is None or not self.pictures:
            return
        self._show_plan()
        if self.space_short(self.plan.total_bytes):
            self._update_dest()
            return
        self.running = True
        self._cancel.clear()
        self.copy_button.setEnabled(False)
        self.choose_button.setEnabled(False)
        self.bar.setValue(0)
        self.bar.show()
        self.result_label.clear()
        self.started.emit()
        self._job = _CopyJob(self.plan, self._cancel, self._sig, self.clock)
        self._pool.start(self._job)

    def cancel(self) -> None:
        if self.running:
            self._cancel.set()
            self.plan_label.setText("Cancelling…")
        else:
            self.reject()

    def _on_progress(self, done: int, total: int) -> None:
        self.bar.setValue(done * BAR_STEPS // total if total else BAR_STEPS)
        self.progress.emit(done, total)

    def _on_done(self, res) -> None:
        self.running = False
        self.result = res
        self._job = None
        copied = set(res.copied)
        pics = sum(1 for p in self.pictures if any(f in copied for f in p.files))
        text = (
            f"Copied {pics} picture{'s' if pics != 1 else ''} ({len(res.copied)} files)"
            f" · skipped {len(res.skipped)} · failed {len(res.failed)}"
        )
        if res.cancelled:
            text += " · cancelled"
        self.result_label.setText(text)
        self.log_label.setText(f"Log: {res.log_path}" if res.log_path else "The log could not be written")
        self.bar.setValue(BAR_STEPS if not res.cancelled else self.bar.value())
        self.plan_label.clear()
        self.cancel_button.hide()
        self.copy_button.hide()
        self.close_button.show()
        self.show_button.show()
        self.choose_button.setEnabled(True)
        self.finished.emit(res)

    def show_folder(self) -> None:
        if self.dest is not None:
            self.open_url(QUrl.fromLocalFile(str(self.dest)))

    def _ask_close(self) -> bool:
        box = QMessageBox.question(
            self, "Copy in progress", "Stop copying and close?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        return box == QMessageBox.StandardButton.Yes

    def wait_idle(self, timeout_ms: int = 10000) -> bool:
        """Block until the worker thread has finished; bounded by timeout_ms."""
        return self._pool.waitForDone(timeout_ms)

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self.running:
            if not self.confirm_close():
                ev.ignore()
                return
            self._cancel.set()
            self._pool.waitForDone(10000)
            self.running = False  # QDialog.closeEvent calls reject(); it must not ask again
        super().closeEvent(ev)

    def reject(self) -> None:
        if self.running:
            self.close()  # Esc mid-copy goes through the same question
            return
        super().reject()
