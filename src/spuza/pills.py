"""Pills: user-defined coloured labels, up to nine, each on a digit 1-9 (task spp-model).

No Qt at import (the default folder asks QStandardPaths lazily, like session.py). Two small JSON files live in
`pills_dir()`: `pills.json` (the PillBook: name, colour, digit) and `index.json` (the PillIndex: which pills a
picture on a card carries, keyed per file so a reloaded card finds them). The card itself is never written (dec-053).
Both files are written atomically (temp file + os.replace) and read tolerantly: a missing or corrupt file is an
empty book or index, and a failed save returns False instead of raising (a full disk must not stop a cull).

The index is bounded: entries not CHANGED for PRUNE_DAYS (365; reading with get() does not refresh) are dropped and at most MAX_ENTRIES (200 000) are
kept (the most recently touched), both applied on save; a file over MAX_BYTES is not read.

Stars (1-5, 0 = cleared in Spuza) live in the same index entries as `stars` beside `names`: PillIndex.stars() /
set_stars(). An entry with stars and no pills is kept; a bad `stars` value on load is dropped, the names stay.
"""

from __future__ import annotations

import json
import logging
import os
import math
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from spuza import capture

log = logging.getLogger(__name__)

ENV_DIR = "SPUZA_PILLS_DIR"  # overrides pills_dir(); the tests set it so they never touch real app data
MAX_PILLS = 9
PRUNE_DAYS = 365
MAX_ENTRIES = 200_000
MAX_BYTES = 64 * 1024 * 1024  # a file bigger than this is not ours (or not sane): ignored
VERSION = 1


@dataclass(frozen=True)
class Swatch:
    name: str
    finder: int  # Finder's colour index 0..7
    dark: str  # hex on a dark theme
    light: str  # hex on a light theme


SWATCHES: tuple[Swatch, ...] = (
    Swatch("None", 0, "#d9d9d9", "#3c3c3c"),
    Swatch("Gray", 1, "#8e8e96", "#85858d"),
    Swatch("Green", 2, "#8fd14f", "#4f9a14"),
    Swatch("Purple", 3, "#b266e6", "#8e44c7"),
    Swatch("Blue", 4, "#2fb4e8", "#1480b8"),
    Swatch("Yellow", 5, "#f2d12e", "#cfa400"),
    Swatch("Red", 6, "#ff4d4d", "#d92d20"),
    Swatch("Orange", 7, "#ff8a1f", "#e0690a"),
)


def swatch(name: str) -> Swatch:
    """The swatch called `name` (case-insensitive); an unknown name gives the None swatch."""
    want = str(name).strip().casefold()
    for s in SWATCHES:
        if s.name.casefold() == want:
            return s
    return SWATCHES[0]


def _rgb(hex_: str):
    try:
        h = str(hex_).strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            return None
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def nearest_swatch(hex_: str) -> Swatch:
    """The swatch whose dark or light colour is nearest in RGB; an unparseable colour gives the None swatch."""
    rgb = _rgb(hex_)
    if rgb is None:
        return SWATCHES[0]
    best, best_d = SWATCHES[0], None
    for s in SWATCHES:
        for ref in (s.dark, s.light):
            r = _rgb(ref)
            d = sum((a - b) ** 2 for a, b in zip(rgb, r))
            if best_d is None or d < best_d:
                best, best_d = s, d
    return best


def canonical_color(color) -> str:
    """The canonical swatch NAME for `color`: a swatch name (any case) or a hex (nearest swatch); else ValueError."""
    if isinstance(color, str):
        want = color.strip().casefold()
        for s in SWATCHES:
            if s.name.casefold() == want:
                return s.name
        if _rgb(color) is not None:
            return nearest_swatch(color).name
    raise ValueError(f"not a swatch name or hex colour: {color!r}")


@dataclass(frozen=True)
class Pill:
    name: str
    color: str  # always a canonical swatch name ("None", "Gray", ... "Orange"); see canonical_color
    digit: int


def pills_dir() -> Path:
    """`<QStandardPaths.AppDataLocation>/pills` (never created here); $SPUZA_PILLS_DIR wins."""
    env = os.environ.get(ENV_DIR)
    if env:
        return Path(env)
    try:
        from PySide6.QtCore import QStandardPaths

        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    except Exception:
        base = ""
    return (Path(base) if base else Path.home() / ".spuza") / "pills"


def _now() -> float:
    return time.time()


def _read_json(path: Path):
    """The parsed JSON at `path`, or None when missing, too big, unreadable or corrupt. Never raises."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        return json.loads(path.read_bytes().decode("utf-8"))
    except Exception:  # OSError, ValueError, RecursionError on absurd nesting: all mean "nothing usable"
        return None


def _write_json(path: Path, doc) -> bool:
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:
        log.warning("could not save %s: %s", path, e)
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def _clean_name(name) -> str:
    n = str(name).strip()
    if not n:
        raise ValueError("a pill needs a name")
    if "\n" in n or "\r" in n:
        raise ValueError("a pill name cannot contain a line break")
    return n


class PillBook:
    """The user's pills: at most nine, each with a unique (case-insensitive) name and a digit 1-9."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else pills_dir() / "pills.json"
        self._by_digit: dict[int, Pill] = {}

    def pills(self) -> list[Pill]:
        return [self._by_digit[d] for d in sorted(self._by_digit)]

    def by_digit(self, digit: int) -> Pill | None:
        return self._by_digit.get(digit)

    def by_name(self, name: str) -> Pill | None:
        want = str(name).strip().casefold()
        for p in self._by_digit.values():
            if p.name.casefold() == want:
                return p
        return None

    def create(self, name: str, color: str) -> Pill:
        name = _clean_name(name)
        color = canonical_color(color)
        if self.by_name(name) is not None:
            raise ValueError(f"there is already a pill called {name}")
        free = [d for d in range(1, MAX_PILLS + 1) if d not in self._by_digit]
        if not free:
            raise ValueError(f"at most {MAX_PILLS} pills")
        pill = Pill(name, color, free[0])
        self._by_digit[pill.digit] = pill
        return pill

    def rename(self, old: str, new: str) -> Pill:
        cur = self.by_name(old)
        if cur is None:
            raise ValueError(f"no pill called {old}")
        new = _clean_name(new)
        other = self.by_name(new)
        if other is not None and other.digit != cur.digit:
            raise ValueError(f"there is already a pill called {new}")
        pill = replace(cur, name=new)
        self._by_digit[pill.digit] = pill
        return pill

    def recolor(self, name: str, color: str) -> Pill:
        cur = self.by_name(name)
        if cur is None:
            raise ValueError(f"no pill called {name}")
        pill = replace(cur, color=canonical_color(color))
        self._by_digit[pill.digit] = pill
        return pill

    def delete(self, name: str) -> None:
        cur = self.by_name(name)
        if cur is not None:
            del self._by_digit[cur.digit]

    def adopt(self, tags) -> list[Pill]:
        """Add the tags (.name/.hex) whose names are unknown while a digit is free; returns the pills added."""
        added: list[Pill] = []
        for t in tags:
            try:
                name = _clean_name(t.name)
            except ValueError:
                continue
            if self.by_name(name) is not None:
                continue
            try:
                added.append(self.create(name, nearest_swatch(t.hex).name))
            except ValueError:
                break  # the book is full
        return added

    def tag(self, name: str):
        """The XMP tag for pill `name` (spuza.xmp.PillTag, imported lazily): its name and the nearest swatch's dark hex."""
        from spuza.xmp import PillTag

        p = self.by_name(name)
        if p is None:
            raise ValueError(f"no pill called {name}")
        return PillTag(p.name, swatch(p.color).dark)

    @classmethod
    def load(cls, path: Path | None = None) -> "PillBook":
        book = cls(path)
        doc = _read_json(book.path)
        rows = doc.get("pills") if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            return book
        for r in rows:
            if not isinstance(r, dict):
                continue
            name, color, digit = r.get("name"), r.get("color"), r.get("digit")
            if not (isinstance(name, str) and isinstance(color, str)):
                continue
            if not (isinstance(digit, int) and not isinstance(digit, bool) and 1 <= digit <= MAX_PILLS):
                continue
            try:
                name, color = _clean_name(name), canonical_color(color)
            except ValueError:
                continue
            if digit in book._by_digit or book.by_name(name) is not None:
                continue
            book._by_digit[digit] = Pill(name, color, digit)
        return book

    def save(self) -> bool:
        doc = {"version": VERSION, "pills": [{"name": p.name, "color": p.color, "digit": p.digit} for p in self.pills()]}
        return _write_json(self.path, doc)


def _valid_stars(v) -> int | None:
    """`v` when it is an int 0..5 (not a bool), else None."""
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 5 else None


def _names(names) -> tuple[str, ...]:
    """Stripped, non-empty names without case-insensitive duplicates, first spelling kept."""
    seen, out = set(), []
    for n in names:
        n = str(n).strip()
        if n and n.casefold() not in seen:
            seen.add(n.casefold())
            out.append(n)
    return tuple(out)


class PillIndex:
    """Which pills each picture carries, keyed per file (capture key + lowercased name + size), kept in app data."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else pills_dir() / "index.json"
        # key -> {"names": [...], "stars": 0..5 (absent: never set in Spuza), "at": epoch seconds of the last change}
        self._entries: dict[str, dict] = {}
        self._keys: dict[str, str] = {}  # primary path -> key, so a file is read once
        self._lock = threading.Lock()  # guards _entries: the UI mutates while a worker may save

    def key(self, picture) -> str:
        primary = os.fspath(picture.primary)
        hit = self._keys.get(primary)
        if hit is not None:
            return hit
        name = Path(primary).name.casefold()
        try:
            st = os.stat(primary)
            size, mtime = st.st_size, int(st.st_mtime)
        except OSError:
            size, mtime = -1, 0
        ck = None
        if size >= 0:
            try:
                ck = capture.capture_key(primary)
            except Exception:
                ck = None
        if ck is not None:
            key = f"{ck.taken}.{ck.subsec}#{ck.counter}|{name}|{size}"
        else:
            key = f"{name}|{size}|{mtime}"
        self._keys[primary] = key
        return key

    def get(self, picture) -> tuple[str, ...]:
        k = self.key(picture)
        with self._lock:
            e = self._entries.get(k)
            return tuple(e["names"]) if e else ()

    def _put(self, k: str, names) -> None:  # caller holds the lock
        kept = _names(names)
        old = self._entries.get(k)
        stars = old.get("stars") if old else None
        if kept or stars is not None:
            e = {"names": list(kept), "at": _now()}
            if stars is not None:
                e["stars"] = stars
            self._entries[k] = e
        else:
            self._entries.pop(k, None)

    def stars(self, picture) -> int | None:
        """The stars set in Spuza: None = never set, 0 = cleared, 1..5."""
        k = self.key(picture)
        with self._lock:
            e = self._entries.get(k)
            return _valid_stars(e.get("stars")) if e else None

    def set_stars(self, picture, n: int | None) -> None:
        """Set 0..5, or None to forget the stars. Raises ValueError for anything else."""
        if n is not None and _valid_stars(n) is None:
            raise ValueError(f"stars must be None or 0..5, not {n!r}")
        k = self.key(picture)
        with self._lock:
            e = self._entries.get(k)
            names = list(e["names"]) if e else []
            if n is None:
                if names:
                    self._entries[k] = {"names": names, "at": _now()}
                else:
                    self._entries.pop(k, None)
            else:
                self._entries[k] = {"names": names, "stars": n, "at": _now()}

    def set(self, picture, names) -> None:
        k = self.key(picture)
        with self._lock:
            self._put(k, names)

    def toggle(self, picture, name: str) -> bool:
        """Flip `name` on the picture; returns the new state (True = now carries it)."""
        k = self.key(picture)
        want = str(name).strip().casefold()
        with self._lock:
            e = self._entries.get(k)
            cur = list(e["names"]) if e else []
            if any(n.casefold() == want for n in cur):
                self._put(k, [n for n in cur if n.casefold() != want])
                return False
            self._put(k, cur + [name])
            return True

    def clear(self, picture) -> None:
        """Clear the picture's pills (its stars stay)."""
        k = self.key(picture)
        with self._lock:
            self._put(k, [])

    def rename_pill(self, old: str, new: str) -> None:
        want = str(old).strip().casefold()
        with self._lock:
            for e in self._entries.values():
                if any(n.casefold() == want for n in e["names"]):
                    e["names"] = list(_names(new if n.casefold() == want else n for n in e["names"]))
                    e["at"] = _now()

    def drop_pill(self, name: str) -> None:
        want = str(name).strip().casefold()
        with self._lock:
            for k in list(self._entries):
                e = self._entries[k]
                kept = [n for n in e["names"] if n.casefold() != want]
                if len(kept) != len(e["names"]):
                    if kept or e.get("stars") is not None:
                        e["names"], e["at"] = kept, _now()
                    else:
                        del self._entries[k]

    @classmethod
    def load(cls, path: Path | None = None) -> "PillIndex":
        idx = cls(path)
        doc = _read_json(idx.path)
        rows = doc.get("entries") if isinstance(doc, dict) else None
        if not isinstance(rows, dict):
            return idx
        for k, e in rows.items():
            if not (isinstance(k, str) and isinstance(e, dict)):
                continue
            names, at = e.get("names"), e.get("at")
            if not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
                continue
            if not (isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at)):
                continue
            kept = _names(names)
            stars = _valid_stars(e.get("stars"))  # a bad value is dropped; the names stay
            if kept or stars is not None:
                row = {"names": list(kept), "at": float(at)}
                if stars is not None:
                    row["stars"] = stars
                idx._entries[k] = row
        return idx

    def save(self) -> bool:
        """Prune (see the module docstring), then write. The snapshot is taken under the lock, the write is not."""
        with self._lock:
            cutoff = _now() - PRUNE_DAYS * 86400
            live = [(k, e) for k, e in self._entries.items() if e["at"] >= cutoff]
            if len(live) > MAX_ENTRIES:
                live.sort(key=lambda ke: ke[1]["at"], reverse=True)
                live = live[:MAX_ENTRIES]
            self._entries = dict(live)
            snap = {}
            for k, e in live:
                row = {"names": list(e["names"]), "at": e["at"]}
                if e.get("stars") is not None:
                    row["stars"] = e["stars"]
                snap[k] = row
        return _write_json(self.path, {"version": VERSION, "entries": snap})
