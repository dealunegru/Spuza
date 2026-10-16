"""Resume: a card's marks and place, kept in Spuza's own app-data folder, never on the card (flag resume_session).

No Qt at import (the default folder asks QStandardPaths lazily). One small JSON per card under
`<AppDataLocation>/sessions/`, written atomically (temp file + os.replace) so a crash never leaves a torn file.
The key is a fingerprint of the card's file list (relative names plus sizes), so the same card in another reader
restores and a reformatted card does not; each marked picture is also checked by name, size and mtime on restore.
Owned by task spz-nx-resume.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PRUNE_DAYS = 30


@dataclass(frozen=True)
class Restored:
    marks: frozenset[int]  # catalog indices that were marked and still match
    index: int  # the place, clamped to the catalog
    saved_at: float  # epoch seconds of the save
    dropped: int  # marked pictures that no longer match (gone, resized, re-dated)


def default_dir() -> Path:
    """`<QStandardPaths.AppDataLocation>/sessions` (never created here)."""
    raise NotImplementedError


def card_key(root, pictures) -> str:
    """A stable hex fingerprint of the card: the pictures' paths relative to `root`, plus their sizes."""
    raise NotImplementedError


class SessionStore:
    def __init__(self, folder: Path | None = None):
        """`folder` defaults to default_dir(); it is created on the first save, never on read."""
        raise NotImplementedError

    def save(self, key: str, root, pictures, marks, index: int, now: float | None = None) -> None:
        """Write this card's session atomically. Never raises on I/O (a full disk must not stop a cull)."""
        raise NotImplementedError

    def load(self, key: str, root, pictures) -> Restored | None:
        """This card's session matched against `pictures`, or None (none saved, unreadable, nothing matches)."""
        raise NotImplementedError

    def forget(self, key: str) -> None:
        raise NotImplementedError

    def prune(self, max_age_days: int = PRUNE_DAYS, now: float | None = None) -> int:
        """Delete sessions older than `max_age_days`; returns how many."""
        raise NotImplementedError
