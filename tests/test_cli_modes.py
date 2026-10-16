"""--advanced / --classic: this run only, never persisted; decode threads follow the mode."""

import os
from pathlib import Path

import pytest

from spuza import cli, features
from spuza.catalog import Catalog, Prefetcher
from spuza.ui import settings


def ini() -> Path:
    return Path(os.environ["SPUZA_SETTINGS_FILE"])


def mode_for(argv):
    return features.resolve_mode(cli.parse_args(argv).mode, settings.open_settings())


def test_no_flag_means_none_and_classic():
    assert cli.parse_args([]).mode is None
    assert mode_for([]) == "classic"


def test_advanced_flag_enables_and_writes_nothing():
    assert mode_for(["--advanced"]) == "advanced"
    features.set_mode("advanced")
    assert features.enabled("decode_threads")
    assert not ini().exists()


def test_classic_flag_beats_saved_advanced_and_leaves_it():
    prefs = settings.open_settings()
    settings.write_advanced(prefs, True)
    prefs.sync()
    before = ini().read_bytes()
    assert mode_for(["--classic"]) == "classic"
    assert ini().read_bytes() == before
    assert settings.read_advanced(settings.open_settings())


def test_both_flags_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(["--advanced", "--classic"])
    assert e.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_threads_from_classic_ignores_saved_value():
    prefs = settings.open_settings()
    settings.write_threads(prefs, 6)
    assert cli.threads_from(prefs) == 3


def test_threads_from_advanced_uses_saved_value():
    prefs = settings.open_settings()
    settings.write_threads(prefs, 6)
    features.set_mode("advanced")
    assert cli.threads_from(prefs) == 6


def test_threads_from_advanced_default_when_unset():
    features.set_mode("advanced")
    assert cli.threads_from(settings.open_settings()) == 3


def test_prefetcher_threads():
    cat = Catalog([])
    a, b = Prefetcher(cat, threads=5), Prefetcher(cat)
    try:
        assert a.pool.maxThreadCount() == 5
        assert b.pool.maxThreadCount() == 3
    finally:
        a.shutdown()
        b.shutdown()
