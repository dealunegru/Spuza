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
