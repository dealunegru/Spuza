"""Prefetcher window defaults (12 ahead / 8 behind) and the live set_window."""

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication

from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.ui import settings


def pic(i):
    p = Path(f"/x/{i}.jpg")
    return Picture(str(i), p, None, (p,), "JPG", 10, float(i))


def loader(path, max_side):
    return Image.new("RGB", (8, 6), (1, 2, 3)), None


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.002)
    return False


@pytest.fixture
def make(qapp=None):
    made = []

    def f(n=100, **kw):
        cat = Catalog([pic(i) for i in range(n)])
        pf = Prefetcher(cat, loader=loader, **kw)
        made.append(pf)
        return cat, pf

    yield f
    for pf in made:
        pf.shutdown()


def test_defaults_match_settings(make):
    _cat, pf = make()
    assert (pf.ahead, pf.behind) == (12, 8)
    assert (pf.ahead, pf.behind) == (settings.DEFAULT_AHEAD, settings.DEFAULT_BEHIND)


@pytest.mark.parametrize("i,want", [(0, (0, 12)), (50, (42, 62)), (99, (91, 99)), (95, (87, 99))])
def test_window_maths_12_8(make, i, want):
    _cat, pf = make()
    pf.update(i)
    assert pf._window == want


def test_window_maths_behind_zero(make):
    _cat, pf = make(behind=0)
    pf.update(50)
    assert pf._window == (50, 62)


def test_set_window_shrink_evicts_outside_keeps_inside(make):
    cat, pf = make()
    cat.go(50)
    pf.update(50)
    assert pump(lambda: all(pf.cached(k) is not None for k in range(42, 63)))
    pf.set_window(2, 1)
    assert (pf.ahead, pf.behind) == (2, 1)
    assert pf._window == (49, 52)
    assert [k for k in range(42, 63) if pf.cached(k) is not None] == [49, 50, 51, 52]
    assert pf.info(45) is None


def test_set_window_grow_starts_new_jobs(make):
    cat, pf = make(ahead=1, behind=0)
    cat.go(10)
    pf.update(10)
    assert pump(lambda: pf.cached(11) is not None)
    assert pf.cached(15) is None
    pf.set_window(6, 2)
    assert pump(lambda: all(pf.cached(k) is not None for k in range(8, 17)))


def test_set_window_clamps_negatives_to_zero(make):
    _cat, pf = make()
    pf.set_window(-3, -1)
    assert (pf.ahead, pf.behind) == (0, 0)


def test_set_window_after_shutdown_is_noop(make):
    cat, pf = make(n=30)
    pf.update(5)
    pf.shutdown()
    pf.set_window(3, 3)
    assert (pf.ahead, pf.behind) == (12, 8)
    assert pf._window == (0, 17)


def test_busy_is_true_while_decodes_are_pending_and_false_when_idle(make):
    cat, pf = make(n=20)
    assert not pf.busy()
    pf.update(0)
    assert pf.busy()
    assert pump(lambda: not pf.busy())
    assert pf.cached(0) is not None and pf.cached(12) is not None


def test_busy_is_false_after_shutdown(make):
    cat, pf = make(n=20)
    pf.update(0)
    pf.shutdown()
    assert pump(lambda: not pf.busy())
