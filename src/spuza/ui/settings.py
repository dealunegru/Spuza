"""Spuza's persisted preferences: one QSettings store, overridable for tests.

Set SPUZA_SETTINGS_FILE to an .ini path to keep preferences out of the user's registry / plist
(the test suite does, per test). Otherwise the platform store is used: the registry on Windows,
~/Library/Preferences/com.fungimasters.Spuza.plist on macOS, ~/.config on Linux.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings

ORG, APP = "fungimasters", "Spuza"
ADVANCE_ON_MARK = "advance_on_mark"


def open_settings() -> QSettings:
    path = os.environ.get("SPUZA_SETTINGS_FILE")
    if path:
        return QSettings(path, QSettings.Format.IniFormat)
    return QSettings(ORG, APP)


def read_bool(s: QSettings, key: str, default: bool) -> bool:
    v = s.value(key, default)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# The prefetch window: pictures decoded ahead of and kept behind the current one.
PREFETCH_AHEAD = "prefetch/ahead"
PREFETCH_BEHIND = "prefetch/behind"
DEFAULT_AHEAD, DEFAULT_BEHIND = 12, 8
AHEAD_RANGE, BEHIND_RANGE = (1, 64), (0, 64)


def read_int(s: QSettings, key: str, default: int, lo: int, hi: int) -> int:
    """An int preference clamped to lo..hi; a missing or unreadable value gives `default`."""
    try:
        n = int(s.value(key, default))  # an .ini store hands back strings
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def read_window(s: QSettings) -> tuple[int, int]:
    """The saved (ahead, behind), clamped to their ranges; defaults when unset."""
    return (read_int(s, PREFETCH_AHEAD, DEFAULT_AHEAD, *AHEAD_RANGE),
            read_int(s, PREFETCH_BEHIND, DEFAULT_BEHIND, *BEHIND_RANGE))


def write_window(s: QSettings, ahead: int, behind: int) -> None:
    s.setValue(PREFETCH_AHEAD, int(ahead))
    s.setValue(PREFETCH_BEHIND, int(behind))


def window_mb(ahead: int, behind: int, max_side: int = 2560) -> int:
    """About how many MB the cached window holds: ahead + behind + the current picture, each an RGB888
    preview of max_side x (2/3 max_side), so about 13 MB at 2560 px (a 3:2 frame)."""
    return round((ahead + behind + 1) * 2 * max_side * max_side / 1_000_000)
