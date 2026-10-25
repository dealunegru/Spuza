"""Resume controller: restore at card open, save on change, Edit > Start fresh (flag resume_session).

Contract for the window (wired by spz-nx-wire): build one `ResumeController(catalog, root, store)` after the
catalog is scanned; call `restore()` once after the first picture is painted (it fills the catalog's marks and
moves its index, the window then re-renders and shows the returned banner); call `start_fresh()` from Edit > Start fresh and show its
text; call `flush()` from closeEvent. Saving is polled: a QTimer compares (marks, index) with the last save at most
once per `interval_ms` and writes off the GUI thread, only while `features.enabled("resume_session")` and the
"Remember marks" preference is on. In classic mode it neither reads nor writes. Owned by task spz-nx-resume.

The writer is a plain thread that touches no Qt object; one write is in flight at most and a newer snapshot
replaces any waiting one (coalescing). `tick()` is the timer's slot, public so tests can drive it.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, QTimer

from spuza import features
from spuza.ratio import Ratio
from spuza.session import SessionStore, card_key
from spuza.story import Story

UNDO_SECONDS = 10.0
JOIN_SECONDS = 1.0  # the longest flush() / start_fresh() wait for a write in flight
IO_WAIT_SECONDS = 1.0  # the longest start_fresh() waits for the store lock (a stuck write must not freeze the GUI)


class ResumeController(QObject):
    def __init__(self, catalog, root, store=None, interval_ms: int = 1000, clock=None, parent=None,
                 rotations: dict | None = None, crops: dict | None = None, stories: dict | None = None,
                 crop_ratios: dict | None = None, band=None):
        super().__init__(parent)
        self.band = band  # friend round 7: the window's derived.Band, shared by reference (None: no derived pictures)
        self.crop_ratios = crop_ratios if crop_ratios is not None else {}  # friend round 6: index -> Ratio, shared
        self.rotations = rotations if rotations is not None else {}  # contract fr3: the window's dict, shared
        self.crops = crops if crops is not None else {}  # friend round 5: index -> (l, t, r, b), shared the same way
        self.stories = stories if stories is not None else {}  # friend round 5: index -> spuza.story.Story
        self._catalog, self._root = catalog, root
        self._store = store  # built on first use: classic never touches app data
        self._key: str | None = None
        self._clock = clock or time.monotonic
        self._on = True
        self._last = self._snapshot()  # a card opened and left alone writes nothing
        self._pending = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()  # guards _pending and _thread
        self._io = threading.Lock()  # one store operation at a time
        self._undo: tuple[frozenset[int], float, tuple[int, ...]] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, int(interval_ms)))
        self._timer.timeout.connect(self.tick)
        self._timer.start()

    # -- helpers -----------------------------------------------------------------------------------------------
    def _active(self) -> bool:
        return features.enabled("resume_session")

    def _saving(self) -> bool:
        return self._active() and self._on

    def _snapshot(self):
        c = self._catalog
        turned = frozenset((i, t % 4) for i, t in dict(self.rotations).items()
                           if isinstance(t, int) and not isinstance(t, bool) and t % 4)
        cropped = frozenset((i, tuple(r)) for i, r in dict(self.crops).items() if isinstance(i, int) and r)
        storied = frozenset((i, (s.zoom, s.dx, s.dy)) for i, s in dict(self.stories).items() if isinstance(i, int))
        ratioed = frozenset((i, r) for i, r in dict(self.crop_ratios).items() if isinstance(i, int) and isinstance(r, Ratio))
        made = self.band.snapshot() if self.band is not None else ()
        return frozenset(i for i in range(len(c)) if c.is_marked(i)), c.index, turned, cropped, storied, ratioed, made

    def _get_store(self) -> SessionStore:
        if self._store is None:
            self._store = SessionStore()
        return self._store

    def _get_key(self) -> str:
        if self._key is None:
            self._key = card_key(self._root, self._catalog.pictures)
        return self._key

    def _save(self, snap) -> None:
        extra = {"rotations": dict(snap[2])} if snap[2] else {}  # a store without the keyword still saves marks
        if snap[3]:
            extra["crops"] = dict(snap[3])
        if snap[4]:
            extra["stories"] = {i: Story(*t) for i, t in snap[4]}
        if snap[5]:
            extra["crop_ratios"] = dict(snap[5])
        if self.band is not None:
            extra["derived"] = snap[6]
        self._get_store().save(self._get_key(), self._root, self._catalog.pictures, snap[0], snap[1], **extra)

    def _write(self, snap) -> None:
        with self._io:
            self._save(snap)

    def _write_bounded(self, snap) -> bool:
        """_write, but give up (False) when the store lock is not free within IO_WAIT_SECONDS."""
        if not self._io.acquire(timeout=IO_WAIT_SECONDS):
            return False
        try:
            self._save(snap)
        finally:
            self._io.release()
        return True

    def _run(self) -> None:
        while True:
            with self._lock:
                snap, self._pending = self._pending, None
                if snap is None:
                    self._thread = None
                    return
            try:
                self._write(snap)
            except Exception:
                pass

    def _submit(self, snap) -> None:
        with self._lock:
            self._pending = snap
            if self._thread is not None:
                return
            self._thread = t = threading.Thread(target=self._run, name="spuza-resume", daemon=True)
        t.start()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait (at most `timeout`, default JOIN_SECONDS) for the writer to drain; True when it has."""
        with self._lock:
            t = self._thread
        if t is not None:
            t.join(JOIN_SECONDS if timeout is None else timeout)
        with self._lock:
            return self._thread is None

    # -- API ---------------------------------------------------------------------------------------------------
    def set_enabled(self, on: bool) -> None:
        """The "Remember marks between runs" preference, live. Off: stop saving (the file stays)."""
        self._on = bool(on)
        if not self._on:
            with self._lock:
                self._pending = None

    def tick(self) -> None:
        """Timer slot: queue a write when (marks, index) differs from the last save."""
        if not self._saving():
            return
        snap = self._snapshot()
        if snap != self._last:
            self._last = snap
            self._submit(snap)

    def restore(self) -> str | None:
        """Apply this card's saved session to the catalog; the banner text, e.g. "Restored 37 marks and your place
        (picture 212) from 16 Oct 21:14 · Edit > Start fresh to clear the marks", or None when nothing was restored."""
        if not self._saving():
            return None
        c = self._catalog
        with self._io:
            r = self._get_store().load(self._get_key(), self._root, c.pictures)
        if r is None:
            return None
        for i in r.marks:
            c.set_mark(i, True)
        c.go(r.index)
        self.rotations.clear()
        self.rotations.update(r.rotations)
        self.crops.clear()
        self.crops.update(r.crops)
        self.crop_ratios.clear()
        self.crop_ratios.update(r.crop_ratios)
        self.stories.clear()
        self.stories.update({i: Story(*t) for i, t in r.stories.items()})
        if self.band is not None:
            self.band.reset(r.derived)
        self._last = self._snapshot()
        n = len(r.marks)
        k = len(r.rotations)
        parts = ([f"{n} mark{'s' if n != 1 else ''}"] if n else []) + ([f"{k} rotation{'s' if k != 1 else ''}"] if k else [])
        parts += [f"{len(d)} {word}{'s' if len(d) != 1 else ''}" for d, word in ((r.crops, "crop"), (r.stories, "story fit"))
                  if d]
        k = len(r.derived)
        if k and not r.crops and not r.stories:
            parts.append(f"{k} cropped picture{'s' if k != 1 else ''}")
        what = " and ".join(parts + ["your place"])
        when = datetime.fromtimestamp(r.saved_at).strftime("%d %b %H:%M")
        extra = f" ({r.dropped} changed or missing skipped)" if r.dropped else ""
        return f"Restored {what} (picture {c.index + 1}) from {when}{extra} · Edit > Start fresh to clear the marks"

    def start_fresh(self) -> str:
        """Edit > Start fresh: clear this card's marks and forget its session; Start fresh again within 10 s undoes it.
        The status text."""
        if not self._active():
            return ""
        c = self._catalog
        now = self._clock()
        if self._undo is not None:
            marks, at, made = self._undo
            self._undo = None
            if now - at <= UNDO_SECONDS:
                for i in marks:
                    c.set_mark(i, True)
                if self.band is not None:
                    for i in made:
                        self.band.set_marked(i, True)  # a derived picture removed meanwhile is skipped
                self._last = snap = self._snapshot()
                if self._on:
                    self.wait_idle()
                    if not self._write_bounded(snap):
                        self._last = None  # the next tick queues it
                return f"Marks back ({len(marks) + len(made)})"
        marks = self._snapshot()[0]
        for i in marks:
            c.set_mark(i, False)
        made = tuple(d.id for d in self.band.items if d.marked) if self.band is not None else ()
        for i in made:  # friend round 7: derived pictures carry their own marks; Start fresh clears those too (they stay)
            self.band.set_marked(i, False)
        with self._lock:
            self._pending = None
        self.wait_idle()
        if self._io.acquire(timeout=IO_WAIT_SECONDS):
            try:
                self._get_store().forget(self._get_key())
            finally:
                self._io.release()
            # rotations, crops and story fits outlive U (only the marks go): the next tick saves them
            self._last = self._snapshot() if not (self.rotations or self.crops or self.stories or self.band) else None
            busy = ""
        else:  # a write is stuck: do not freeze the GUI behind it; the next tick saves the cleared state instead
            busy = " (the saved session is busy; it is replaced when it frees)"
        total = len(marks) + len(made)
        self._undo = (marks, now, made) if total else None
        if not total:
            return "Started fresh · nothing was marked" + busy
        word = "mark" if total == 1 else "marks"
        return f"Started fresh: {total} {word} cleared{busy} · Start fresh again within 10 s to undo"

    def flush(self) -> None:
        """Write now if anything changed since the last save (closeEvent). Blocks at most ~1 s."""
        if not self._saving():
            return
        snap = self._snapshot()
        if snap != self._last:
            self._last = snap
            self._submit(snap)
        self.wait_idle()
