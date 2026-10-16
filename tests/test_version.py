"""The reported version is the installed package's, i.e. pyproject's; a literal in __init__ drifts (0.1.2 printed 0.1.0)."""

import tomllib
from pathlib import Path

import spuza


def test_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert spuza.__version__ == pyproject["project"]["version"]
