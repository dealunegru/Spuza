"""Several mounted cards: choosing one, pairing dual-slot twins, and the words that say so."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # catalog pulls in Qt; keep `import spuza.cards` (and the CLI's startup) light
    from spuza.catalog import Picture


class CardError(ValueError):
    """`--card` named something that is not a mounted card."""


def card_root(card: Path) -> Path:
    """The folder to open for a card: its DCIM, else the card itself."""
    card = Path(card)
    return card / "DCIM" if (card / "DCIM").is_dir() else card


def pick_card(cards: list[Path], spec: str | None) -> int:
    """Index into `cards` for `--card` (1-based number, or a path of a mounted card); None means the first."""
    if spec is None:
        return 0
    text = str(spec).strip()
    if text.isdigit():
        n = int(text)
        if 1 <= n <= len(cards):
            return n - 1
        raise CardError(f"--card {n}: there are {len(cards)} card(s), numbered 1..{len(cards)}")
    want = _norm(text)
    for i, c in enumerate(cards):
        if _norm(str(c)) == want:
            return i
    raise CardError(f"--card {text}: not a mounted card (found: {', '.join(str(c) for c in cards) or 'none'})")


def _norm(text: str) -> str:
    return text.rstrip("\\/").replace("\\", "/").lower() or text


def list_lines(cards: list[Path], chosen: int) -> list[str]:
    """One line per card for stderr, the opened one marked."""
    return [f"{'*' if i == chosen else ' '} {i + 1}: {c}" + ("  (opened)" if i == chosen else "")
            for i, c in enumerate(cards)]


def pair_twins(first: list[Picture], second: list[Picture]) -> tuple[list[Picture], list[Picture]]:
    """A second-card picture with a first-card picture's primary name (any case) and size is its twin.

    Returns (first, with `twins` set where a twin was found; second's pictures that have no twin).
    Each second-card picture pairs at most once."""
    pool: dict[tuple[str, int], list[Picture]] = {}
    for p in second:
        pool.setdefault((p.primary.name.lower(), p.size_bytes), []).append(p)
    taken: set[int] = set()
    paired: list[Picture] = []
    for p in first:
        cands = pool.get((p.primary.name.lower(), p.size_bytes))
        if cands:
            t = cands.pop(0)
            taken.add(id(t))
            p = dataclasses.replace(p, twins=t.files)
        paired.append(p)
    return paired, [p for p in second if id(p) not in taken]


def describe(cards: list[Path], chosen: int, twins: int = 0, only_there: int = 0) -> str:
    """Status-line note for several cards; empty for one."""
    if len(cards) < 2:
        return ""
    others = [(i, c) for i, c in enumerate(cards) if i != chosen]
    text = f"Card {chosen + 1} of {len(cards)} ({cards[chosen]})"
    for i, c in others:
        text += f" · {c} also mounted"
        if len(cards) == 2:
            text += f": {twins} twins, {only_there} only there — spuza --card {i + 1}"
        else:
            text += f" (spuza --card {i + 1})"
    return text


def catalog_note(catalog) -> str:
    """Counts of non-picture and unreadable files, empty when both are zero."""
    parts = []
    others, bad = len(getattr(catalog, "others", ())), len(getattr(catalog, "unreadable", ()))
    if others:
        parts.append(f"{others} other files")
    if bad:
        parts.append(f"{bad} unreadable")
    return " · ".join(parts)
