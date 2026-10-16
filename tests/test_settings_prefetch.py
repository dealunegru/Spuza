"""The prefetch window preferences: keys, defaults, clamping and the memory estimate."""

from __future__ import annotations

from spuza.ui import settings


def test_defaults_and_keys():
    assert (settings.DEFAULT_AHEAD, settings.DEFAULT_BEHIND) == (12, 8)
    assert (settings.PREFETCH_AHEAD, settings.PREFETCH_BEHIND) == ("prefetch/ahead", "prefetch/behind")
    assert settings.AHEAD_RANGE == (1, 64) and settings.BEHIND_RANGE == (0, 64)


def test_unset_store_reads_defaults():
    assert settings.read_window(settings.open_settings()) == (12, 8)


def test_round_trip_through_a_fresh_store():
    s = settings.open_settings()
    settings.write_window(s, 20, 0)
    s.sync()
    del s
    assert settings.read_window(settings.open_settings()) == (20, 0)


def test_out_of_range_and_garbage_are_clamped_or_defaulted():
    s = settings.open_settings()
    s.setValue(settings.PREFETCH_AHEAD, 500)
    s.setValue(settings.PREFETCH_BEHIND, "lots")
    assert settings.read_window(s) == (64, 8)
    s.setValue(settings.PREFETCH_AHEAD, 0)
    s.setValue(settings.PREFETCH_BEHIND, -3)
    assert settings.read_window(s) == (1, 0)


def test_window_mb_counts_current_picture_at_about_13_mb_each():
    assert settings.window_mb(12, 8) == 275  # 21 pictures x 2560 x 1707 x 3 bytes
    assert settings.window_mb(8, 3) == 157
    assert settings.window_mb(1, 0, max_side=1280) == 7
