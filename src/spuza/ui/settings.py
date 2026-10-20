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
THEME = "view/theme"  # "dark" or "light": a manual pick; absent means no pick yet


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


# Classic / advanced mode (spuza.features) and the advanced-only decode-thread knob.
MODE_ADVANCED = "mode/advanced"
PREFETCH_THREADS = "prefetch/threads"
DEFAULT_THREADS = 3
THREADS_RANGE = (1, 8)


def read_advanced(s: QSettings) -> bool:
    return read_bool(s, MODE_ADVANCED, False)


def write_advanced(s: QSettings, on: bool) -> None:
    s.setValue(MODE_ADVANCED, bool(on))


def read_threads(s: QSettings) -> int:
    """The saved decode-thread count, clamped to THREADS_RANGE; DEFAULT_THREADS when unset."""
    return read_int(s, PREFETCH_THREADS, DEFAULT_THREADS, *THREADS_RANGE)


def write_threads(s: QSettings, n: int) -> None:
    s.setValue(PREFETCH_THREADS, int(n))


# Next-features round (spuza-features-r1). All four are advanced-only flags (spuza.features).
PEAKING_ON = "peaking/on"  # P: the overlay is shown; off by default
PEAKING_THRESHOLD = "peaking/threshold"  # edge strength 1-255 on the luminance gradient
DEFAULT_PEAKING_THRESHOLD = 60
PEAKING_THRESHOLD_RANGE = (1, 255)
FOCUS_MARKER = "focus/marker"  # O: the ring at the camera's AF point; off by default
RESUME_ON = "resume/enabled"  # "Remember marks between runs"; on by default (advanced mode only)


def read_peaking_on(s: QSettings) -> bool:
    return read_bool(s, PEAKING_ON, False)


def write_peaking_on(s: QSettings, on: bool) -> None:
    s.setValue(PEAKING_ON, bool(on))


def read_peaking_threshold(s: QSettings) -> int:
    return read_int(s, PEAKING_THRESHOLD, DEFAULT_PEAKING_THRESHOLD, *PEAKING_THRESHOLD_RANGE)


def write_peaking_threshold(s: QSettings, n: int) -> None:
    s.setValue(PEAKING_THRESHOLD, int(n))


def read_focus_marker(s: QSettings) -> bool:
    return read_bool(s, FOCUS_MARKER, False)


def write_focus_marker(s: QSettings, on: bool) -> None:
    s.setValue(FOCUS_MARKER, bool(on))


def read_resume_on(s: QSettings) -> bool:
    return read_bool(s, RESUME_ON, True)


def write_resume_on(s: QSettings, on: bool) -> None:
    s.setValue(RESUME_ON, bool(on))


# Friend round 2: what a copy does with the destination and the card (classic Preferences rows; all on by default).
COPY_ORGANISE = "copy/organise"  # raw / soc / ed folders in the destination
COPY_EJECT = "copy/eject"  # eject the card after a successful copy
COPY_QUIT = "copy/quit"  # quit Spuza after a successful copy
COPY_ROTATION = "copy/rotation"  # friend round 3: write a picture's rotation into the copy's EXIF (never the original)


def read_organise(s: QSettings) -> bool:
    return read_bool(s, COPY_ORGANISE, True)


def write_organise(s: QSettings, on: bool) -> None:
    s.setValue(COPY_ORGANISE, bool(on))


def read_eject_after(s: QSettings) -> bool:
    return read_bool(s, COPY_EJECT, True)


def write_eject_after(s: QSettings, on: bool) -> None:
    s.setValue(COPY_EJECT, bool(on))


def read_quit_after(s: QSettings) -> bool:
    return read_bool(s, COPY_QUIT, True)


def write_quit_after(s: QSettings, on: bool) -> None:
    s.setValue(COPY_QUIT, bool(on))



def read_write_rotation(s: QSettings) -> bool:
    return read_bool(s, COPY_ROTATION, True)


def write_write_rotation(s: QSettings, on: bool) -> None:
    s.setValue(COPY_ROTATION, bool(on))


# Friend round 3: what Z shows on a shot the camera cropped (classic Preferences row; off by default).
ZOOM_FULL_SENSOR = "zoom/full_sensor"


def read_zoom_full_sensor(s: QSettings) -> bool:
    return read_bool(s, ZOOM_FULL_SENSOR, False)


def write_zoom_full_sensor(s: QSettings, on: bool) -> None:
    s.setValue(ZOOM_FULL_SENSOR, bool(on))
