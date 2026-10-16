"""Resume controller: restore at card open, save on change, U to start fresh (flag resume_session).

Contract for the window (wired by spz-nx-wire): build one `ResumeController(catalog, root, store)` after the
catalog is scanned; call `restore()` once after the first picture is painted (it fills the catalog's marks and
moves its index, the window then re-renders and shows the returned banner); call `start_fresh()` on U and show its
text; call `flush()` from closeEvent. Saving is polled: a QTimer compares (marks, index) with the last save at most
once per `interval_ms` and writes off the GUI thread, only while `features.enabled("resume_session")` and the
"Remember marks" preference is on. In classic mode it neither reads nor writes. Owned by task spz-nx-resume.
"""

from __future__ import annotations

from PySide6.QtCore import QObject


class ResumeController(QObject):
    def __init__(self, catalog, root, store=None, interval_ms: int = 1000, clock=None, parent=None):
        super().__init__(parent)
        raise NotImplementedError

    def set_enabled(self, on: bool) -> None:
        """The "Remember marks between runs" preference, live. Off: stop saving (the file stays)."""
        raise NotImplementedError

    def restore(self) -> str | None:
        """Apply this card's saved session to the catalog; the banner text, e.g. "Restored 37 marks and your place
        (picture 212) from 16 Oct 21:14 · U to start fresh", or None when nothing was restored."""
        raise NotImplementedError

    def start_fresh(self) -> str:
        """U: clear this card's marks and forget its session; U again within 10 s undoes it. The status text."""
        raise NotImplementedError

    def flush(self) -> None:
        """Write now if anything changed since the last save (closeEvent). Blocks at most ~1 s."""
        raise NotImplementedError
