"""Spuza: fast photo culling for camera cards."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("spuza")
except PackageNotFoundError:  # running from a plain checkout without an install
    __version__ = "0+unknown"
