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

from spuza import derived as derived_mod
from spuza import ratio as ratio_mod
from spuza.ratio import Ratio
from spuza.story import Story

PRUNE_DAYS = 30
VERSION = 2  # 2 adds "rotations" beside the marks; a version 1 session (no rotations) still loads.
# Friend round 5 adds "crops" and "stories" rows beside them WITHOUT a bump: a reader that does not know them ignores
# them and keeps the marks, and a session without them loads as before. Friend round 6 adds an optional "ratio" string
# to a crop row (ratio.encode; absent for Free), again without a bump; a crop and a story may now both be kept.
# Friend round 7 adds a top-level "derived" list (derived.to_row plus the source picture's name/size/mtime), again
# without a bump. A session without that key (0.9.0) is read as derived pictures made from its crop and story rows.
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
    crops: dict[int, tuple] = field(default_factory=dict, hash=False)  # catalog index -> (left, top, right, bottom)
    stories: dict[int, tuple] = field(default_factory=dict, hash=False)  # catalog index -> (zoom, dx, dy)
    crop_ratios: dict[int, Ratio] = field(default_factory=dict, hash=False)  # crop's index -> its Ratio (Free: absent)
    derived: tuple = ()  # derived.Derived items in saved order (a 0.9.0 session: its crops and stories, converted)


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


def _finite(v) -> bool:
    return _number(v) and abs(v) != float("inf")


def _crop_rect(v):
    """(l, t, r, b) as floats when `v` is four finite fractions in 0..1 with a side of at least 1 %, else None."""
    try:
        if len(v) != 4 or not all(_finite(x) for x in v):
            return None
        left, top, right, bottom = (float(x) for x in v)
    except TypeError:
        return None
    if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
        return None
    if right - left < 0.01 or bottom - top < 0.01:
        return None
    return (left, top, right, bottom)


def _story_triple(zoom, dx, dy):
    """(zoom, dx, dy) as floats when finite with zoom >= 1 (the fit) and the offsets within a few frames, else None."""
    if not all(_finite(x) for x in (zoom, dx, dy)):
        return None
    if not (1.0 <= zoom <= 1000.0 and abs(dx) <= 10.0 and abs(dy) <= 10.0):
        return None
    return (float(zoom), float(dx), float(dy))


class SessionStore:
    def __init__(self, folder: Path | None = None):
        """`folder` defaults to default_dir(); it is created on the first save, never on read."""
        self.folder = Path(folder) if folder is not None else default_dir()

    def _path(self, key: str) -> Path | None:
        return self.folder / f"{key}.json" if _valid_key(key) else None

    def save(self, key: str, root, pictures, marks, index: int, now: float | None = None,
             rotations=None, crops=None, stories=None, crop_ratios=None, derived=None) -> None:
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
            cropped = []
            for i in sorted(i for i in (crops or {}) if isinstance(i, int) and not isinstance(i, bool)):
                rect = _crop_rect(crops[i]) if 0 <= i < n else None
                if rect is not None:
                    p = pictures[i]
                    row = {"name": _rel(root, p), "size": int(p.size_bytes), "mtime": float(p.mtime),
                           "rect": list(rect)}
                    r = (crop_ratios or {}).get(i)
                    if isinstance(r, Ratio) and r != ratio_mod.FREE:
                        row["ratio"] = ratio_mod.encode(r)
                    cropped.append(row)
            storied = []
            for i in sorted(i for i in (stories or {}) if isinstance(i, int) and not isinstance(i, bool)):
                if not 0 <= i < n:
                    continue
                st = stories[i]
                tri = _story_triple(getattr(st, "zoom", None), getattr(st, "dx", None), getattr(st, "dy", None))
                if tri is not None:
                    p = pictures[i]
                    storied.append({"name": _rel(root, p), "size": int(p.size_bytes), "mtime": float(p.mtime),
                                    "zoom": tri[0], "dx": tri[1], "dy": tri[2]})
            made = []
            for d in derived or ():
                i = getattr(d, "source", None)
                if (isinstance(d, derived_mod.Derived) and isinstance(i, int) and not isinstance(i, bool)
                        and 0 <= i < n and derived_mod.from_row(derived_mod.to_row(d), i) is not None):
                    p = pictures[i]
                    made.append({"name": _rel(root, p), "size": int(p.size_bytes), "mtime": float(p.mtime),
                                 **derived_mod.to_row(d)})
            idx = max(0, min(int(index), n - 1)) if n else 0
            doc = {"version": VERSION, "root": str(root), "saved_at": time.time() if now is None else float(now),
                   "index": idx, "index_name": _rel(root, pictures[idx]) if n else "", "marks": rows,
                   "rotations": turned, "crops": cropped, "stories": storied}
            if derived is not None:  # absent keeps a legacy-kwarg save readable as 0.9.0 wrote it
                doc["derived"] = made
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
        def matches(r) -> int | None:  # the row's picture index when name, size and mtime still match
            i = by_name.get(r["name"])
            if (i is not None and _number(r["size"]) and r["size"] == pictures[i].size_bytes
                    and _number(r["mtime"]) and abs(r["mtime"] - pictures[i].mtime) <= MTIME_TOLERANCE):
                return i
            return None

        cropped, storied, ratios = {}, {}, {}
        for r in doc.get("crops") if isinstance(doc.get("crops"), list) else ():
            try:
                i, rect = matches(r), _crop_rect(r["rect"])
                if i is not None and rect is not None:
                    cropped[i] = rect
                    dec = ratio_mod.decode(r.get("ratio"))  # missing or garbage: the crop stays, as Free
                    if dec is not None and dec != ratio_mod.FREE:
                        ratios[i] = dec
            except (KeyError, TypeError, AttributeError):
                pass
        for r in doc.get("stories") if isinstance(doc.get("stories"), list) else ():
            try:
                i, tri = matches(r), _story_triple(r["zoom"], r["dx"], r["dy"])
                if i is not None and tri is not None:
                    storied[i] = tri
            except (KeyError, TypeError):
                pass
        if isinstance(doc.get("derived"), list):
            made = []
            for r in doc["derived"]:
                try:
                    i = matches(r)
                except (KeyError, TypeError, AttributeError):
                    i = None
                d = derived_mod.from_row(r, i) if i is not None else None
                if d is None:
                    dropped += 1
                else:
                    made.append(d)
        elif "derived" in doc:
            made = []
        else:  # a 0.9.0 session: one derived picture per crop, then per story fit, marked as their original is
            made = []
            for i in sorted(set(cropped) | set(storied)):
                if i in cropped:
                    made.append(derived_mod.Derived(len(made) + 1, i, derived_mod.CROP, crop=cropped[i],
                                                    ratio=ratios.get(i), marked=i in marks))
                if i in storied:
                    made.append(derived_mod.Derived(len(made) + 1, i, derived_mod.STORY, story=Story(*storied[i]),
                                                    marked=i in marks))
        n = len(pictures)
        name, num = doc.get("index_name"), doc.get("index")
        if isinstance(name, str) and name in by_name:
            index = by_name[name]
        elif isinstance(num, int) and not isinstance(num, bool):
            index = max(0, min(num, n - 1)) if n else 0
        else:
            index = 0
        if not marks and not turned and not cropped and not storied and not made and index == 0:
            return None  # nothing worth restoring
        return Restored(frozenset(marks), index, float(saved_at), dropped, turned, cropped, storied, ratios, tuple(made))

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
