"""Copy sheet: shows what will be copied, where, and whether it fits; then copies on a worker.

Closing mid-copy: the sheet ASKS first ("Stop copying and close?"). Yes cancels the copy (the half
written file is removed by ``run_copy``, finished files stay) and closes; No keeps copying.
``confirm_close`` and ``choose_folder`` are attributes so tests can inject them.

After a successful copy (no failed file, not cancelled) the sheet can eject the card (``eject_after``, off the
UI thread, bounded by eject's own timeout) and ask to quit (``quit_after``): one status line, a ``Stay`` button, and a
countdown of ``countdown_seconds`` that ends by emitting ``quit_requested`` (the window closes itself). A failed
eject does not stop the quit. ``eject_fn`` is an attribute so tests can inject it. Any error or cancel: nothing
is ejected and nothing quits.

``progress(done, total)`` is in KiB (rounded up) so it fits a Qt int signal for any card size.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
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
from spuza import crop as crop_mod
from spuza import eject as eject_mod
from spuza import story as story_mod
from spuza.catalog import KNOWN_EXTS
from spuza.copy import human_size

MIN_PROGRESS_INTERVAL = 1 / 30  # seconds between progress emits (about 30 per second)
BAR_STEPS = 1000
AMBER = "#d98a00"
NL = chr(10)


def _kib(n: int) -> int:
    return (n + 1023) >> 10


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _paths_tip(paths: list[Path], limit: int = 200) -> str:
    lines = [str(p) for p in paths[:limit]]
    if len(paths) > limit:
        lines.append(f"... and {len(paths) - limit} more")
    return NL.join(lines)


def _show_name() -> str:
    import sys

    return "Show in Finder" if sys.platform == "darwin" else "Show in Explorer"


class _Signals(QObject):
    progress = Signal(int, int)
    done = Signal(object)
    ejected = Signal(bool, str, int)  # ok, reason, generation of the copy it belongs to


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


class _EjectJob(QRunnable):
    def __init__(self, fn, target: Path, sig: _Signals, gen: int):
        super().__init__()
        self.fn, self.target, self.sig, self.gen = fn, target, sig, gen
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            ok, why = self.fn(self.target)
        except Exception as exc:  # noqa: BLE001 - eject must never kill the thread
            ok, why = False, f"{type(exc).__name__}: {exc}"
        self.sig.ejected.emit(bool(ok), str(why), self.gen)


class CopySheet(QDialog):
    started = Signal()
    progress = Signal(int, int)
    finished = Signal(object)  # shadows QDialog.finished(int) on purpose (contract); see done()

    quit_requested = Signal()  # the countdown ran out: the window closes itself

    def __init__(self, pictures, default_dest=None, parent=None, others=(), unreadable=(), *,
                 organise: bool = True, eject_after: bool = False, quit_after: bool = False,
                 eject_target: Path | None = None, rotations=None, write_rotation: bool = True, crops=None,
                 stories=None):
        super().__init__(parent)
        self.rotations = dict(rotations or {})  # contract fr3: Picture.primary -> clockwise quarter turns
        self.write_rotation = bool(write_rotation)
        # contract fr5: Picture.primary -> Crop (fractions of the EXIF-oriented picture, before the user's turns);
        # each is normalised and a garbage entry is dropped. The crop file always has the picture's turns baked in.
        self.crops = {}
        for k, v in dict(crops or {}).items():
            c = crop_mod.normalise(v)
            if c is not None:
                self.crops[k] = c
        # contract fr5: Picture.primary -> spuza.story.Story; an entry that is not a Story is dropped. Each exports as
        # <dest>/ed/<stem>_story.jpg (1080x1920) with the picture's turns baked in.
        self.stories = {k: v for k, v in dict(stories or {}).items() if isinstance(v, story_mod.Story)}
        self.organise = bool(organise)
        self.eject_after = bool(eject_after)
        self.quit_after = bool(quit_after)
        self.eject_target: Path | None = Path(eject_target) if eject_target else None
        self.eject_fn: Callable = eject_mod.eject
        self.countdown_seconds = 3
        self.countdown_interval_ms = 1000
        self._remaining = 0
        self._dismissed = False  # closed by hand: a late eject result must not start a quit
        self._status_base = ""
        self._countdown = QTimer(self)
        self._countdown.timeout.connect(self._tick)
        self.setWindowTitle("Copy marked pictures")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(560, 520)
        self.pictures = list(pictures)
        self.others: list[Path] = [Path(o) for o in (others or ())]
        self.unreadable: list[Path] = [Path(u) for u in (unreadable or ())]
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
        self._sig.ejected.connect(self._on_ejected)
        self._eject_job = None
        self._eject_pool = QThreadPool()  # its own pool: a later copy is never queued behind a slow eject
        self._eject_pool.setMaxThreadCount(1)
        self._gen = 0  # bumped by a new copy, a new destination and close: an older eject result is then ignored
        self._job = None
        self._closing = False  # closed mid-copy: finish closing when the worker's result arrives

        self.total_files = sum(len(p.files) for p in self.pictures)
        self.total_bytes = sum(p.size_bytes for p in self.pictures)  # includes companions
        self.total_companions = sum(len(getattr(p, "companions", ())) for p in self.pictures)
        self.total_twins = sum(len(getattr(p, "twins", ())) for p in self.pictures)

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
            f"{len(self.pictures)} pictures · {self.total_files} files"
            + (f" ({_n(self.total_companions, 'companion', 'companions')})" if self.total_companions else "")
            + f" · {human_size(self.total_bytes)}"
        )
        self.notes_label = QLabel()
        self.notes_label.setWordWrap(True)
        self.notes_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._fill_notes()
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
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self.stay_button = QPushButton("Stay")
        self.stay_button.setAutoDefault(False)
        self.stay_button.clicked.connect(self.stay)
        self.stay_button.hide()
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
        buttons.addWidget(self.stay_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.copy_button)
        lay = QVBoxLayout(self)
        lay.addWidget(self.title_label)
        lay.addLayout(where)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.total_label)
        lay.addWidget(self.notes_label)
        lay.addWidget(self.free_label)
        lay.addWidget(self.plan_label)
        lay.addWidget(self.bar)
        lay.addWidget(self.result_label)
        lay.addWidget(self.log_label)
        lay.addWidget(self.status_label)
        lay.addLayout(buttons)
        self._update_dest()

    def _twin_note(self) -> str:
        if not self.total_twins:
            return ""
        return f"{_n(self.total_twins, 'twin file', 'twin files')} on the second card not copied (same as the first)"

    def _fill_notes(self) -> None:
        """Say what stays behind: other files (not pictures) and unreadable ones; the paths are in the tooltip."""
        parts, tips = [], []
        if self.others:
            n = len(self.others)
            parts.append(
                f"{n} other file on the card is not a picture and is not copied"
                if n == 1
                else f"{n} other files on the card are not pictures and are not copied"
            )
            tips.append("Not copied (not pictures):" + NL + _paths_tip(self.others))
        if self.unreadable:
            n = len(self.unreadable)
            parts.append(f"{n} file could not be read" if n == 1 else f"{n} files could not be read")
            tips.append("Could not be read:" + NL + _paths_tip(self.unreadable))
        self.notes_label.setText(NL.join(parts))
        self.notes_label.setToolTip((NL + NL).join(tips))

    # ---- destination ---------------------------------------------------------------------------
    def pick_folder(self) -> None:
        start = str(self.dest) if self.dest else ""
        chosen = self.choose_folder(self, "Choose a folder for the copies", start)
        if chosen:
            self.dest = Path(chosen)
            if self.result is not None and not self.running:
                self._reset_for_new_destination()
            self._update_dest()

    def _reset_for_new_destination(self) -> None:
        """After a finished copy, a new folder gets a fresh sheet: the same marks can go to a second drive."""
        self.result = None
        self._gen += 1
        self._stop_countdown()
        self.status_label.hide()
        self.stay_button.hide()
        self.result_label.clear()
        self.log_label.clear()
        self.bar.hide()
        self.bar.setValue(0)
        self.close_button.hide()
        self.show_button.hide()
        self.cancel_button.show()
        self.copy_button.show()

    def _free(self) -> int | None:
        if self.dest is None:
            return None
        try:
            return int(self.free_space_fn(self.dest))
        except OSError:
            return None

    def space_short(self, needed: int | None = None) -> bool:
        free = self._free()
        if needed is None:
            needed = self.plan.total_bytes if self.plan is not None else self.total_bytes
        return free is not None and needed > free

    def _update_dest(self) -> None:
        self.plan = None
        self.plan_label.clear()
        if self.dest is None:
            self.dest_label.setText("No folder chosen")
            self.free_label.setText("")
            self.copy_button.setEnabled(False)
            return
        self.dest_label.setText(str(self.dest))
        if self.pictures:  # plan once: the bytes still to write (not the identical ones) decide "short"
            self.plan = self._plan()
        free = self._free()
        short = self.space_short()
        if free is None:
            self.free_label.setText("Free space unknown")
            self.free_label.setStyleSheet("")
        elif short:
            self.free_label.setText(
                f"Not enough space: need {human_size(self.plan.total_bytes)}, {human_size(free)} free here"
            )
            self.free_label.setStyleSheet(f"color: {AMBER}; font-weight: bold;")
        else:
            self.free_label.setText(f"{human_size(free)} free at the destination")
            self.free_label.setStyleSheet("")
        self.copy_button.setEnabled(bool(self.pictures) and not self.running and not short)
        if self.pictures and not short:
            self._show_plan(self.plan)

    def _turns(self) -> dict[Path, int]:
        """Source image file -> clockwise quarter turns to write into its copy (empty when the write is off).
        A picture's turns go to its picture files (a raw and its JPEG twin), never to its companions."""
        out: dict[Path, int] = {}
        if not self.write_rotation:
            return out
        for p in self.pictures:
            t = self.rotations.get(getattr(p, "primary", None), 0)
            t = t % 4 if isinstance(t, int) and not isinstance(t, bool) else 0
            if t:
                skip = set(getattr(p, "companions", ()))
                out.update({f: t for f in p.files if f not in skip and f.suffix.lower() in KNOWN_EXTS})
        return out

    def _crop_jobs(self) -> list:
        """One job per marked picture that has a crop; the turns are the picture's own, whatever write_rotation says."""
        jobs = []
        for p in self.pictures:
            c = self.crops.get(getattr(p, "primary", None))
            if c is None:
                continue
            t = self.rotations.get(p.primary, 0)
            t = t % 4 if isinstance(t, int) and not isinstance(t, bool) else 0
            jobs.append(crop_mod.CropJob(p.primary, getattr(p, "sidecar", None), c, t))
        return jobs

    def _story_jobs(self) -> list:
        """One job per marked picture that has a story fit; turns baked in, like a crop's."""
        jobs = []
        for p in self.pictures:
            s = self.stories.get(getattr(p, "primary", None))
            if s is None:
                continue
            t = self.rotations.get(p.primary, 0)
            t = t % 4 if isinstance(t, int) and not isinstance(t, bool) else 0
            jobs.append(story_mod.StoryJob(p.primary, getattr(p, "sidecar", None), s, t))
        return jobs

    def _plan(self):
        return copy_mod.plan_copy([p.files for p in self.pictures], self.dest, self.organise, self._turns(),
                                  self._crop_jobs(), self._story_jobs())

    def _show_plan(self, plan=None) -> None:
        self.plan = plan or self._plan()
        skip = sum(1 for it in self.plan.items if it.action == "skip")
        text = f"Will copy {len(self.plan.items) - skip} files, skip {skip} already there"
        if self.plan.crop_items:
            text += f" · {len(self.plan.crop_items)} cropped into {copy_mod.ED_DIR} (size estimated)"
        if self.plan.story_items:
            text += f" · {len(self.plan.story_items)} story fit into {copy_mod.ED_DIR} (size estimated)"
        if self.total_twins:
            text += f" · {self._twin_note()}"
        self.plan_label.setText(text)

    # ---- copying -------------------------------------------------------------------------------
    def start_copy(self) -> None:
        if self.running or self.dest is None or not self.pictures:
            return
        self._show_plan()
        if self.space_short(self.plan.total_bytes):
            self._update_dest()
            return
        self.running = True
        self._dismissed = False
        self._gen += 1
        self._stop_countdown()
        self.status_label.hide()
        self.stay_button.hide()
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
        done_ok = copied | set(res.skipped)
        pics = sum(1 for p in self.pictures if all(f in done_ok for f in p.files))
        partial = sum(
            1 for p in self.pictures if any(f in copied for f in p.files) and not all(f in done_ok for f in p.files)
        )
        comps = {c for p in self.pictures for c in getattr(p, "companions", ())}
        n_comp = sum(1 for f in res.copied if f in comps)
        comp_text = f", {_n(n_comp, 'companion', 'companions')}" if n_comp else ""
        text = (
            f"Copied {pics} picture{'s' if pics != 1 else ''} ({len(res.copied)} files{comp_text})"
            f" · skipped {len(res.skipped)} · failed {len(res.failed)}"
        )
        if partial:
            text += f" · {partial} picture{'s' if partial != 1 else ''} not fully copied"
        if res.cancelled:
            text += " · cancelled"
        if self.total_twins:
            text += f" · {self._twin_note()}"
        cropped = getattr(res, "cropped", ())
        if cropped:
            text += f" · cropped {len(cropped)} into {copy_mod.ED_DIR}"
        stories = getattr(res, "stories", ())
        if stories:
            text += f" · story {len(stories)} into {copy_mod.ED_DIR}"
        rot = self._rotation_text(res)
        if rot:
            text += f" · {rot}"
        where = self._where_text(res)
        if where:
            text += f" · {where}"
        if self.others:
            text += f" · {_n(len(self.others), 'other file', 'other files')} not copied (not a picture)"
        if self.unreadable:
            text += f" · {_n(len(self.unreadable), 'file', 'files')} could not be read"
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
        if self._closing:
            self._closing = False
            self.close()  # running is False now, so this closes without asking again
            return
        if not res.failed and not res.cancelled:
            self._after_copy()

    def _rotation_text(self, res) -> str:
        """Which copies got a new orientation and which kept their original one (and the first reason why)."""
        rotated, kept = getattr(res, "rotated", ()), getattr(res, "rotation_kept", ())
        if not rotated and not kept:
            return ""
        text = f"rotation written to {_n(len(rotated), 'file', 'files')}"
        if kept:
            names = ", ".join(f"{src.name} ({why})" for src, why in kept[:3])
            more = f" and {len(kept) - 3} more" if len(kept) > 3 else ""
            text += f" · {_n(len(kept), 'file', 'files')} kept the original orientation: {names}{more}"
        return text

    def _where_text(self, res) -> str:
        """With organise on: how many copied files went to raw and to soc."""
        plan = self.plan
        if plan is None or not getattr(plan, "organise", False):
            return ""
        folder = {it.src: it.dst.parent.name for it in plan.items}
        counts = {copy_mod.RAW_DIR: 0, copy_mod.SOC_DIR: 0}
        for f in res.copied:
            if folder.get(f) in counts:
                counts[folder[f]] += 1
        return f"raw {counts[copy_mod.RAW_DIR]}, soc {counts[copy_mod.SOC_DIR]}"

    # ---- after a good copy: eject, then quit -----------------------------------------------------
    def _after_copy(self) -> None:
        if self.eject_after and self.eject_target is None:
            self._status("Not opened from a card: nothing ejected.")
            self._maybe_quit()
        elif self.eject_after:
            self._status("Ejecting the card…")
            self._eject_job = _EjectJob(self.eject_fn, self.eject_target, self._sig, self._gen)
            self._eject_pool.start(self._eject_job)
        else:
            self._maybe_quit()

    def _on_ejected(self, ok: bool, why: str, gen: int = -1) -> None:
        if gen != self._gen:
            return  # stale: another copy, destination or close happened since
        self._eject_job = None
        self._status("Card ejected." if ok else f"Could not eject the card: {why}.")
        self._maybe_quit()

    def _status(self, text: str) -> None:
        self._status_base = text
        self.status_label.setText(text)
        self.status_label.show()

    def _maybe_quit(self) -> None:
        if not self.quit_after or self._dismissed or self.running:
            return
        self._remaining = max(0, int(self.countdown_seconds))
        self._show_countdown()
        if self._remaining == 0:
            self._finish_countdown()
            return
        self.stay_button.show()
        self._countdown.start(self.countdown_interval_ms)

    def _show_countdown(self) -> None:
        sep = " " if self._status_base else ""
        self.status_label.setText(f"{self._status_base}{sep}Quitting in {self._remaining} s")
        self.status_label.show()

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._finish_countdown()
        else:
            self._show_countdown()

    def _finish_countdown(self) -> None:
        self._countdown.stop()
        self.stay_button.hide()
        self.quit_requested.emit()

    def _stop_countdown(self) -> None:
        self._countdown.stop()

    def stay(self) -> None:
        """Cancel the pending quit; the sheet stays with its result."""
        self._stop_countdown()
        self.stay_button.hide()
        self.status_label.setText(self._status_base or "Staying open")

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
        return self._pool.waitForDone(timeout_ms) and self._eject_pool.waitForDone(timeout_ms)

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self.running:
            if self._closing or not self.confirm_close():
                ev.ignore()
                return
            # never wait on the GUI thread: cancel, hide, and finish closing when ``done`` arrives
            self._closing = True
            self._cancel.set()
            self.hide()
            ev.ignore()
            return
        self._dismissed = True
        self._gen += 1
        self._stop_countdown()  # closing the sheet by hand also cancels a pending quit
        super().closeEvent(ev)

    def reject(self) -> None:
        if self.running:
            self.close()  # Esc mid-copy goes through the same question
            return
        super().reject()
