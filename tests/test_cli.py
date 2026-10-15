"""Argument parsing and the card-choice rule. Nothing here opens a window."""

from __future__ import annotations

import pytest

from spuza import __version__
from spuza.cli import choose_root, main, parse_args


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"spuza {__version__}"


def test_defaults():
    a = parse_args([])
    assert a.path is None and a.demo is None and not a.bench


def test_positional_path():
    assert parse_args(["/x/y"]).path == "/x/y"


def test_demo_and_bench():
    a = parse_args(["--demo", "/x", "--bench"])
    assert a.demo == "/x" and a.bench


def test_bench_requires_demo(capsys):
    with pytest.raises(SystemExit) as e:
        parse_args(["--bench"])
    assert e.value.code == 2
    assert "--demo" in capsys.readouterr().err


def test_unknown_flag_exits_2():
    with pytest.raises(SystemExit) as e:
        parse_args(["--nope"])
    assert e.value.code == 2


def test_choose_root_single_card_uses_dcim(tmp_path):
    card = tmp_path / "X"
    (card / "DCIM").mkdir(parents=True)
    assert choose_root([card]) == card / "DCIM"


def test_choose_root_several_cards_uses_first_card_root(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    for c in (a, b):
        (c / "DCIM").mkdir(parents=True)
    assert choose_root([a, b]) == a


def test_choose_root_no_card_is_none():
    assert choose_root([]) is None


def test_main_missing_demo_folder_exits_1(tmp_path, capsys):
    assert main(["--demo", str(tmp_path / "nope")]) == 1
    assert "not a folder" in capsys.readouterr().err
