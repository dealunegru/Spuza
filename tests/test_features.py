"""Mode resolution, the flag registry and the frozen classic flag set."""

import pytest

from spuza import features
from spuza.ui import settings

# Promoting a flag to classic flips classic=True in features.FLAGS and edits this line on purpose.
CLASSIC_SET = frozenset()


def test_default_is_classic_with_empty_settings():
    assert features.resolve_mode(None, settings.open_settings()) == features.CLASSIC


def test_saved_advanced_gives_advanced():
    prefs = settings.open_settings()
    settings.write_advanced(prefs, True)
    assert features.resolve_mode(None, prefs) == features.ADVANCED


def test_cli_beats_saved():
    prefs = settings.open_settings()
    settings.write_advanced(prefs, True)
    assert features.resolve_mode("classic", prefs) == features.CLASSIC
    settings.write_advanced(prefs, False)
    assert features.resolve_mode("advanced", prefs) == features.ADVANCED


def test_flag_typo_raises_keyerror():
    with pytest.raises(KeyError):
        features.enabled("decode_thread")


def test_classic_flag_set_is_frozen():
    assert features.classic_flags() == CLASSIC_SET


def test_every_flag_has_a_one_line_description():
    for name, flag in features.FLAGS.items():
        assert flag.name == name
        assert flag.description.strip() and "\n" not in flag.description


def test_enabled_follows_mode():
    assert not features.enabled("decode_threads")
    features.set_mode("advanced")
    assert features.enabled("decode_threads")


def test_conftest_resets_mode_between_tests():
    assert features.mode() == features.CLASSIC
