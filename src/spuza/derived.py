"""Derived pictures: a crop or story fit made with Enter is its own filmstrip picture, right after its original. Pure Python.

The original stays clean; any number of derived pictures hang off one original; each can be marked, and the marked
ones export to ed/ on copy. A derived picture is addressed by ``(source, id)``: ``source`` is the original's catalog
index, ``id`` is unique inside a ``Band`` and never reused while the Band lives.

API
    CROP, STORY                          the two kinds
    Derived(id, source, kind, crop, ratio, story, marked, pills, stars)   frozen; ``pills`` (names) and ``stars``
                                         (None/0/1..5) are the picture's own marks, never the source's; a crop item carries ``crop`` (fractions of the
                                         UN-turned frame, via crop.normalise) and ``ratio`` (None = Free); a story
                                         item carries a ``story.Story``
    Row = (source, id | None)            one filmstrip row; id None is the original
    Band                                 the mutable list of derived pictures, in band order (by source, then by when
                                         each was made); see the class for add / replace / remove / set_marked / reset
                                         / of / get / rows / row_of / version / snapshot
    tag(d, turns=0) -> str               the badge text: "4:5", "crop" (Free), "9:16"
    export_tail(d, turns=0) -> str       what follows the stem in ed/: "_4x5.jpg", ".jpg", "_story.jpg"
    to_row(d) -> dict, from_row(row, source) -> Derived | None    session round trip; from_row never raises
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace as _dc_replace

from spuza import ratio as ratio_mod
from spuza.crop import Crop, normalise
from spuza.ratio import Ratio
from spuza.story import Story

CROP = "crop"
STORY = "story"
_KINDS = (CROP, STORY)

Row = tuple[int, "int | None"]


@dataclass(frozen=True)
class Derived:
    id: int
    source: int
    kind: str
    crop: Crop | None = None
    ratio: Ratio | None = None
    story: Story | None = None
    marked: bool = True
    pills: tuple = ()  # the pill NAMES this picture carries (its own; the source's are never inherited)
    stars: int | None = None  # None never rated, 0 cleared, 1..5


def _int(v, floor: int) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= floor else None


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        v = float(v)
    except OverflowError:
        return None
    return v if math.isfinite(v) else None


def _valid(d) -> Derived | None:
    """``d`` with a normalised crop, or None when it is not a well-formed derived picture."""
    if not isinstance(d, Derived) or _int(d.id, 1) is None or _int(d.source, 0) is None:
        return None
    if not isinstance(d.marked, bool) or not (d.ratio is None or isinstance(d.ratio, Ratio)):
        return None
    if not isinstance(d.pills, tuple) or not all(isinstance(n, str) for n in d.pills):
        return None
    if d.stars is not None and (_int(d.stars, 0) is None or d.stars > 5):
        return None
    if d.kind == CROP:
        rect = normalise(d.crop)
        if rect is None:
            return None
        return d if rect == d.crop and isinstance(d.crop, tuple) else _dc_replace(d, crop=rect)
    if d.kind == STORY:
        return d if isinstance(d.story, Story) else None
    return None


class Band:
    """Every derived picture of one folder, in band order. Mutable: the window owns it and shares it by reference."""

    def __init__(self, items=()):
        self._items: list[Derived] = []
        self._next = 1
        self._version = 0
        self._drop_caches()
        if items:
            self.reset(items)
            self._version = 0

    # -- reading

    @property
    def items(self) -> tuple[Derived, ...]:
        return tuple(self._items)

    @property
    def version(self) -> int:
        return self._version

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(tuple(self._items))

    def __bool__(self) -> bool:
        return bool(self._items)

    def of(self, source) -> tuple[Derived, ...]:
        return self._by_source().get(source, ())

    def get(self, id) -> Derived | None:
        for d in self._items:
            if d.id == id:
                return d
        return None

    def snapshot(self) -> tuple:
        return tuple(self._items)

    # -- changing

    def add(self, source, kind, *, crop=None, ratio=None, story=None, marked=True) -> Derived:
        """Make a derived picture after the last one of ``source``; ValueError for anything not well formed."""
        d = _valid(Derived(self._next, source, kind, crop, ratio, story, marked))
        if d is None:
            raise ValueError(f"not a derived picture: source={source!r} kind={kind!r}")
        self._insert(d)
        self._next = d.id + 1
        self._touch()
        return d

    def replace(self, id, /, **changes) -> Derived | None:
        """New content for ``id`` (crop, ratio, story, marked); same id, source, kind and place. None if refused."""
        old = self.get(id)
        if old is None or not set(changes) <= {"crop", "ratio", "story", "marked", "pills", "stars"}:
            return None
        new = _valid(_dc_replace(old, **changes))
        if new is None:
            return None
        self._items[self._items.index(old)] = new
        self._touch()
        return new

    def remove(self, id) -> Derived | None:
        d = self.get(id)
        if d is None:
            return None
        self._items.remove(d)
        self._touch()
        return d

    def set_marked(self, id, on) -> bool:
        d = self.get(id)
        if d is None:
            return False
        if d.marked != bool(on):
            self.replace(id, marked=bool(on))
        return True

    def set_pills(self, id, names) -> bool:
        """The pill names of ``id`` (de-duplicated, order kept); False when there is no such picture or bad names."""
        try:
            kept = tuple(dict.fromkeys(names))
        except TypeError:
            return False
        d = self.get(id)
        if d is None:
            return False
        if d.pills != kept:
            return self.replace(id, pills=kept) is not None
        return True

    def set_stars(self, id, n) -> bool:
        """The stars of ``id`` (0 clears, None un-sets); False when there is no such picture or ``n`` is not 0..5."""
        d = self.get(id)
        if d is None:
            return False
        if d.stars != n:
            return self.replace(id, stars=n) is not None
        return True

    def rename_pill(self, old, new) -> None:
        """A pill was renamed in the book: every derived picture carrying it carries the new name."""
        want = str(old).strip().casefold()
        for d in list(self._items):
            if any(n.casefold() == want for n in d.pills):
                self.set_pills(d.id, [new if n.casefold() == want else n for n in d.pills])

    def drop_pill(self, name) -> None:
        """A pill was deleted from the book: no derived picture carries it."""
        want = str(name).strip().casefold()
        for d in list(self._items):
            if any(n.casefold() == want for n in d.pills):
                self.set_pills(d.id, [n for n in d.pills if n.casefold() != want])

    def reset(self, items) -> None:
        """Restore from a session: ids and relative order kept, duplicates and invalid items dropped."""
        kept: list[Derived] = []
        seen: set[int] = set()
        try:
            incoming = list(items or ())
        except TypeError:
            incoming = []
        for raw in incoming:
            d = _valid(raw)
            if d is not None and d.id not in seen:
                seen.add(d.id)
                kept.append(d)
        kept.sort(key=lambda d: d.source)  # stable: relative order inside one original is kept
        self._items = kept
        self._next = max(seen, default=0) + 1
        self._touch()

    # -- filmstrip rows

    def rows(self, n) -> tuple[Row, ...]:
        """Original then its derived, for each of ``n`` originals. Cached until the Band changes."""
        n = max(0, int(n))
        hit = self._rows
        if hit is not None and hit[0] == n:
            return hit[1]
        by = self._by_source()
        out: list[Row] = []
        rev: dict[Row, int] = {}
        for i in range(n):
            rev[(i, None)] = len(out)
            out.append((i, None))
            for d in by.get(i, ()):
                rev[(i, d.id)] = len(out)
                out.append((i, d.id))
        rows = tuple(out)
        self._rows = (n, rows, rev)
        return rows

    def row_of(self, source, id=None, n=None) -> int | None:
        """Index of ``(source, id)`` among ``rows(n)``; ``n`` (positional or keyword) is required. O(1) once cached."""
        if n is None:
            raise TypeError("row_of needs n, the number of originals")
        self.rows(n)
        try:
            return self._rows[2].get((source, id))
        except TypeError:
            return None

    # -- internals

    def _insert(self, d: Derived) -> None:
        at = len(self._items)
        while at > 0 and self._items[at - 1].source > d.source:
            at -= 1
        self._items.insert(at, d)

    def _touch(self) -> None:
        self._version += 1
        self._drop_caches()

    def _drop_caches(self) -> None:
        self._rows = None
        self._groups: dict[int, tuple[Derived, ...]] | None = None

    def _by_source(self) -> dict[int, tuple[Derived, ...]]:
        if self._groups is None:
            tmp: dict[int, list[Derived]] = {}
            for d in self._items:
                tmp.setdefault(d.source, []).append(d)
            self._groups = {k: tuple(v) for k, v in tmp.items()}
        return self._groups


def tag(d: Derived, turns: int = 0) -> str:
    if d.kind == STORY:
        return "9:16"
    return "crop" if d.ratio is None else ratio_mod.label(d.ratio, turns)


def export_tail(d: Derived, turns: int = 0) -> str:
    if d.kind == STORY:
        return "_story.jpg"
    return f"{ratio_mod.export_suffix(d.ratio or ratio_mod.FREE, turns)}.jpg"


def to_row(d: Derived) -> dict:
    row: dict = {"id": d.id, "kind": d.kind, "marked": d.marked}
    if d.pills:
        row["pills"] = list(d.pills)
    if d.stars is not None:
        row["stars"] = d.stars
    if d.kind == STORY:
        s = d.story
        row.update(zoom=s.zoom, dx=s.dx, dy=s.dy)
        return row
    row["rect"] = list(d.crop)
    if d.ratio is not None:
        row["ratio"] = ratio_mod.encode(d.ratio)
    return row


def from_row(row, source) -> Derived | None:
    try:
        return _from_row(row, source)
    except (RecursionError, OverflowError, ValueError, TypeError):
        return None


def _from_row(row, source) -> Derived | None:
    if not isinstance(row, dict) or _int(source, 0) is None:
        return None
    id_, kind = _int(row.get("id"), 1), row.get("kind")
    if id_ is None or kind not in _KINDS:
        return None
    marked = row.get("marked", True)
    if not isinstance(marked, bool):
        marked = True
    raw = row.get("pills")
    pills = tuple(dict.fromkeys(n for n in raw if isinstance(n, str))) if isinstance(raw, (list, tuple)) else ()
    stars = _int(row.get("stars"), 0)
    stars = stars if stars is not None and stars <= 5 else None
    if kind == STORY:
        z, dx, dy = (_num(row.get(k)) for k in ("zoom", "dx", "dy"))
        if z is None or dx is None or dy is None or z <= 0:
            return None
        return Derived(id_, source, STORY, story=Story(z, dx, dy), marked=marked, pills=pills, stars=stars)
    rect = row.get("rect")
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        return None
    vals = [_num(v) for v in rect]
    crop = None if any(v is None for v in vals) else normalise(vals)
    if crop is None:
        return None
    return Derived(id_, source, CROP, crop=crop, ratio=ratio_mod.decode(row.get("ratio")), marked=marked,
                   pills=pills, stars=stars)
