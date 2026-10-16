"""--ahead / --behind: parsing, override per flag, never persisted."""

import pytest

from spuza import cli
from spuza.ui import settings


def test_defaults_none():
    a = cli.parse_args([])
    assert a.ahead is None and a.behind is None


@pytest.mark.parametrize("flag,val", [("--ahead", "1"), ("--ahead", "64"), ("--behind", "0"), ("--behind", "64")])
def test_accepts_range_edges(flag, val):
    a = cli.parse_args([flag, val])
    assert getattr(a, flag[2:]) == int(val)


@pytest.mark.parametrize("flag,val", [("--ahead", "0"), ("--ahead", "65"), ("--behind", "-1"),
                                      ("--behind", "65"), ("--ahead", "x"), ("--behind", "1.5"),
                                      ("--ahead", "nan")])
def test_rejects_out_of_range(flag, val, capsys):
    with pytest.raises(SystemExit) as e:
        cli.parse_args([flag, val])
    assert e.value.code == 2
    assert "error" in capsys.readouterr().err


def test_saved_values_used_without_flags():
    prefs = settings.open_settings()
    settings.write_window(prefs, 20, 5)
    assert cli.window_from(cli.parse_args([]), prefs) == (20, 5)


def test_defaults_without_saved_or_flags():
    assert cli.window_from(cli.parse_args([]), settings.open_settings()) == (12, 8)


def test_flag_overrides_per_flag_and_is_not_persisted():
    prefs = settings.open_settings()
    settings.write_window(prefs, 20, 5)
    assert cli.window_from(cli.parse_args(["--ahead", "3"]), prefs) == (3, 5)
    assert cli.window_from(cli.parse_args(["--behind", "0"]), prefs) == (20, 0)
    assert cli.window_from(cli.parse_args(["--ahead", "3", "--behind", "2"]), prefs) == (3, 2)
    prefs.sync()
    assert settings.read_window(settings.open_settings()) == (20, 5)


def test_flags_do_not_create_settings_when_none_saved():
    prefs = settings.open_settings()
    cli.window_from(cli.parse_args(["--ahead", "3"]), prefs)
    prefs.sync()
    assert not prefs.contains(settings.PREFETCH_AHEAD)
    assert not prefs.contains(settings.PREFETCH_BEHIND)


def test_main_passes_window_to_prefetcher_and_saves_nothing(tmp_path, monkeypatch):
    import spuza.catalog as catalog_mod
    from PySide6.QtWidgets import QApplication

    seen = []

    class Rec(catalog_mod.Prefetcher):
        def __init__(self, cat, **kw):
            seen.append(kw)
            super().__init__(cat, **kw)

    monkeypatch.setattr(catalog_mod, "Prefetcher", Rec)
    monkeypatch.setattr(QApplication, "exec", lambda self=None: 0)
    from PIL import Image

    Image.new("RGB", (8, 6)).save(tmp_path / "a.jpg")
    prefs = settings.open_settings()
    settings.write_window(prefs, 20, 5)
    prefs.sync()
    assert cli.main(["--ahead", "3", str(tmp_path)]) == 0
    assert seen == [{"ahead": 3, "behind": 5}]
    assert settings.read_window(settings.open_settings()) == (20, 5)
