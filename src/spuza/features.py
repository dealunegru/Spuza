"""Classic and advanced mode: the feature-flag registry.

Classic is the default and is everything Spuza shipped. A flag with ``classic=False`` is
advanced-only: it is on only while the app runs in advanced mode. Gate UI with
``if features.enabled("name")``. See docs/FEATURE_FLAGS.md for adding and promoting a flag.
"""

from __future__ import annotations

from dataclasses import dataclass

CLASSIC, ADVANCED = "classic", "advanced"
MODES = (CLASSIC, ADVANCED)


@dataclass(frozen=True)
class Flag:
    name: str
    description: str
    classic: bool  # True: part of classic mode (and advanced); False: advanced-only


FLAGS: dict[str, Flag] = {
    f.name: f
    for f in (
        Flag("decode_threads",
             "Decode threads (1-8) in Preferences, saved as prefetch/threads; applied at the next launch."
             " Classic always decodes on 3 threads.",
             classic=False),
    )
}

_mode = CLASSIC


def mode() -> str:
    return _mode


def set_mode(m: str) -> None:
    """Set this run's mode. Never saves anything."""
    global _mode
    if m not in MODES:
        raise ValueError(f"unknown mode {m!r}; expected one of {MODES}")
    _mode = m


def resolve_mode(cli: str | None, prefs) -> str:
    """This run's mode: the CLI choice, else the saved preference, else classic. Reads `prefs`, never writes."""
    if cli in MODES:
        return cli
    from spuza.ui import settings

    return ADVANCED if settings.read_advanced(prefs) else CLASSIC


def classic_flags() -> frozenset[str]:
    """Names of the flags that are part of classic mode."""
    return frozenset(n for n, f in FLAGS.items() if f.classic)


def is_advanced() -> bool:
    return _mode == ADVANCED


def enabled(name: str) -> bool:
    """Whether flag `name` is on in the current mode. An unknown name raises KeyError (a typo is a bug)."""
    try:
        flag = FLAGS[name]
    except KeyError:
        raise KeyError(f"unknown feature flag {name!r}; known: {sorted(FLAGS)}") from None
    return flag.classic or _mode == ADVANCED
