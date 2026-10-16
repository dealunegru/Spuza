"""The contract both mode clusters build on: the settings keys and the features API surface."""

from __future__ import annotations

import pytest

from spuza import features
from spuza.ui import settings


def test_settings_keys_and_ranges():
    assert settings.MODE_ADVANCED == "mode/advanced"
    assert settings.PREFETCH_THREADS == "prefetch/threads"
    assert settings.THREADS_RANGE == (1, 8)
    assert settings.DEFAULT_THREADS == 3


def test_threads_and_mode_round_trip():
    s = settings.open_settings()
    assert settings.read_threads(s) == 3
    assert settings.read_advanced(s) is False
    settings.write_threads(s, 6)
    settings.write_advanced(s, True)
    s.sync()
    s2 = settings.open_settings()
    assert settings.read_threads(s2) == 6
    assert settings.read_advanced(s2) is True
    s2.setValue(settings.PREFETCH_THREADS, 99)
    assert settings.read_threads(s2) == 8


def test_features_surface():
    assert features.CLASSIC == "classic" and features.ADVANCED == "advanced"
    flag = features.FLAGS["decode_threads"]
    assert flag.classic is False and flag.description
    features.set_mode(features.CLASSIC)
    assert features.mode() == "classic" and not features.is_advanced()
    assert features.enabled("decode_threads") is False
    features.set_mode(features.ADVANCED)
    assert features.is_advanced() and features.enabled("decode_threads") is True
    features.set_mode(features.CLASSIC)
    with pytest.raises(KeyError):
        features.enabled("decode_thread")
    with pytest.raises(ValueError):
        features.set_mode("expert")
