"""Shared test setup.

Qt runs headless everywhere (QT_QPA_PLATFORM=offscreen), so the suite passes on CI runners and on a
desktop alike. The real camera files live outside the repo and are read-only; tests that need them
skip when they are absent (CI has none). Set SPUZA_TEST_SET to point elsewhere.
"""

import gc
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# One QApplication for the whole session, made before any test module is imported: a bare
# QGuiApplication made first (test_catalog's fixture) cannot be upgraded and the first widget then
# kills the process. A QApplication serves the QGuiApplication tests too, in any file order or subset.
from PySide6.QtWidgets import QApplication  # noqa: E402

QAPP = QApplication.instance() or QApplication([])

from spuza.ui import gcguard  # noqa: E402

gcguard.install(QAPP)

REAL_SET = Path(os.environ.get("SPUZA_TEST_SET", "E:/tmp/spuza-test/jes"))
RAF_NAME = "20250704_X-Pro2_9268.RAF"
DNG_NAMES = [f"R00451{n}.DNG" for n in range(46, 51)]


@pytest.fixture(autouse=True)
def isolated_prefs(tmp_path, monkeypatch):
    """Every test gets its own preferences file: nothing reaches the registry / plist, nothing leaks."""
    monkeypatch.setenv("SPUZA_SETTINGS_FILE", str(tmp_path / "spuza-prefs.ini"))


@pytest.fixture(autouse=True)
def classic_mode():
    """Mode is module state: every test starts and ends in classic."""
    from spuza import features

    features.set_mode(features.CLASSIC)
    yield
    features.set_mode(features.CLASSIC)


@pytest.fixture(autouse=True)
def gc_on_gui_thread():
    """Free each test's leaked widgets here on the main thread, before the next test starts pool workers."""
    yield
    gc.collect()


@pytest.fixture
def real_set() -> Path:
    """The read-only folder of real camera files; never write inside it."""
    if not (REAL_SET / RAF_NAME).is_file():
        pytest.skip(f"real test set not present at {REAL_SET}")
    return REAL_SET
