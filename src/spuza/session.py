"""Resume: a card's marks and place, kept in Spuza's own app-data folder, never on the card (flag resume_session).

No Qt at import (the default folder asks QStandardPaths lazily). One small JSON per card under
`<AppDataLocation>/sessions/`, written atomically (temp file + os.replace) so a crash never leaves a torn file.
The key is a fingerprint of the card's file list (relative names plus sizes), so the same card in another reader
restores and a reformatted card does not; each marked picture is also checked by name, size and mtime on restore.
Nothing is ever written outside the store folder. Owned by task spz-nx-resume.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

PRUNE_DAYS = 30
VERSION = 2  # 2 adds "rotations" beside the marks; a version 1 session (no rotations) still loads
READ_VERSIONS = (1, 2)
MAX_BYTES = 8 * 1024 * 1024  # a session bigger than this is not ours (or not sane): ignored
MTIME_TOLERANCE = 2.0  # seconds; FAT stores mtime in 2 s steps
ENV_DIR = "SPUZA_SESSIONS_DIR"  # overrides default_dir(); the tests set it so they never touch real app data


@dataclass(frozen=True)
class Restored:
    marks: frozenset[int]  # catalog indices that were marked and still match
    index: int  # the place, clamped to the catalog
    saved_at: float  # epoch seconds of the save
    dropped: int  # marked pictures that no longer match (gone, resized, re-dated)
    rotations: dict[int, int] = field(default_factory=dict, hash=False)  # catalog index -> clockwise quarter turns 1..3


def default_dir() -> Path:
    """`<QStandardPaths.AppDataLocation>/sessions` (never created here); $SPUZA_SESSIONS_DIR wins."""
    env = os.environ.get(ENV_DIR)
    if env:
        return Path(env)
    try:
        from PySide6.QtCore import QStandardPaths

        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    except Exception:
        base = ""
    return (Path(base) if base else Path.home() / ".spuza") / "sessions"


_SEPS = "\\/"


def _rel(root, pic) -> str:
    """The picture's path under `root` with forward slashes (plain string work: 1000 pictures in a few ms)."""
    r, p = os.fspath(root), os.fspath(pic.primary)
    if p.startswith(r) and len(p) > len(r) and (p[len(r)] in _SEPS or r[-1:] in _SEPS):
        return p[len(r):].lstrip(_SEPS).replace("\\", "/")
    return Path(p).name


def card_key(root, pictures) -> str:
    """A stable hex fingerprint of the card: the pictures' paths relative to `root`, plus their sizes."""
    h = hashlib.sha256()
    for name, size in sorted((_rel(root, p), int(p.size_bytes)) for p in pictures):
        h.update(f"{name}\0{size}\n".encode("utf-8", "surrogatepass"))
    return h.hexdigest()


def _valid_key(key) -> bool:
    return isinstance(key, str) and 0 < len(key) <= 128 and key.isalnum()


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


class SessionStore:
    def __init__(self, folder: Path | None = None):
        """`folder` defaults to default_dir(); it is created on the first save, never on read."""
        self.folder = Path(folder) if folder is not None else default_dir()

    def _path(self, key: str) -> Path | None:
        return self.folder / f"{key}.json" if _valid_key(key) else None

    def save(self, key: str, root, pictures, marks, index: int, now: float | None = None,
             rotations=None) -> None:
        """Write this card's session atomically. Never raises on I/O (a full disk must not stop a cull)."""
        path = self._path(key)
        if path is None:
            return
        tmp = None
        try:
            n = len(pictures)
            rows = []
            for i in sorted(marks):
                if 0 <= i < n:
                    p = pictures[i]
                    rows.append({"name": _rel(root, p), "size": int(p.size_bytes), "mtime": float(p.mtime)})
            turned = []
            for i in sorted(rotations or {}):
                t = rotations[i]
                if isinstance(i, int) and 0 <= i < n and isinstance(t, int) and not isinstance(t, bool) and 1 <= t <= 3:
                    p = pictures[i]
                    turned.append({"name": _rel(root, p), "size": int(p.size_bytes), "mtime": float(p.mtime), "turns": t})
            idx = max(0, min(int(index), n - 1)) if n else 0
            doc = {"version": VERSION, "root": str(root), "saved_at": time.time() if now is None else float(now),
                   "index": idx, "index_name": _rel(root, pictures[idx]) if n else "", "marks": rows,
                   "rotations": turned}
            data = json.dumps(doc, separators=(",", ":")).encode("utf-8", "replace")
            self.folder.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=key + ".", suffix=".tmp", dir=self.folder)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            tmp = None
        except Exception:  # OSError, or odd values: a cull must never stop for the session file
            pass
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _read(self, path: Path):
        try:
            if path.stat().st_size > MAX_BYTES:
                return None
            doc = json.loads(path.read_bytes())
        except (OSError, ValueError, RecursionError, MemoryError):
            return None
        if not isinstance(doc, dict) or doc.get("version") not in READ_VERSIONS:
            return None
        return doc

    def load(self, key: str, root, pictures) -> Restored | None:
        """This card's session matched against `pictures`, or None (none saved, unreadable, nothing matches)."""
        path = self._path(key)
        doc = self._read(path) if path is not None else None
        if doc is None:
            return None
        saved_at, rows = doc.get("saved_at"), doc.get("marks")
        if not _number(saved_at) or not isinstance(rows, list):
            return None
        by_name = {_rel(root, p): i for i, p in enumerate(pictures)}
        marks, dropped = set(), 0
        for r in rows:
            try:
                i = by_name.get(r["name"])
                ok = (i is not None and _number(r["size"]) and r["size"] == pictures[i].size_bytes
                      and _number(r["mtime"]) and abs(r["mtime"] - pictures[i].mtime) <= MTIME_TOLERANCE)
            except (KeyError, TypeError):
                ok = False
            if ok:
                marks.add(i)
            else:
                dropped += 1
        turned = {}
        for r in doc.get("rotations") if isinstance(doc.get("rotations"), list) else ():
            try:
                i, t = by_name.get(r["name"]), r["turns"]
                if (i is not None and _number(r["size"]) and r["size"] == pictures[i].size_bytes
                        and _number(r["mtime"]) and abs(r["mtime"] - pictures[i].mtime) <= MTIME_TOLERANCE
                        and isinstance(t, int) and not isinstance(t, bool) and 1 <= t <= 3):
                    turned[i] = t
            except (KeyError, TypeError):
                pass
        n = len(pictures)
        name, num = doc.get("index_name"), doc.get("index")
        if isinstance(name, str) and name in by_name:
            index = by_name[name]
        elif isinstance(num, int) and not isinstance(num, bool):
            index = max(0, min(num, n - 1)) if n else 0
        else:
            index = 0
        if not marks and not turned and index == 0:
            return None  # nothing worth restoring
        return Restored(frozenset(marks), index, float(saved_at), dropped, turned)

    def forget(self, key: str) -> None:
        path = self._path(key)
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass

    def prune(self, max_age_days: int = PRUNE_DAYS, now: float | None = None) -> int:
        """Delete sessions older than `max_age_days`; returns how many."""
        now = time.time() if now is None else now
        cutoff = now - max_age_days * 86400
        gone = 0
        try:
            files = list(self.folder.glob("*.json"))
        except OSError:
            return 0
        for f in files:
            doc = self._read(f)
            at = doc.get("saved_at") if doc else None
            if not _number(at):
                try:
                    at = f.stat().st_mtime  # unreadable or foreign: age by the file itself
                except OSError:
                    continue
            if at < cutoff:
                try:
                    f.unlink()
                    gone += 1
                except OSError:
                    pass
        return gone
