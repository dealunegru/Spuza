"""PeakingOverlay on a real Viewer, offscreen. The mask job is the real one unless a test gates it."""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from spuza import features, peaking
from spuza.ui.peaking_overlay import PeakingOverlay
from spuza.ui.viewer import Viewer


def _checker(w=400, h=300, cell=10) -> QImage:
    yy, xx = np.mgrid[0:h, 0:w]
    g = (((yy // cell) + (xx // cell)) % 2 * 255).astype(np.uint8)
    a = np.dstack([g, g, g])
    return QImage(a.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888).copy()


def _pic(name):
    return SimpleNamespace(primary=Path(name), stem=name)


def _pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    QApplication.processEvents()
    return cond()


def _settle():
    """Let a job that should NOT report get its chance: pump events a little."""
    end = time.monotonic() + 0.1
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


@pytest.fixture
def rig():
    v = Viewer()
    v.resize(800, 600)
    v.show()
    v.needs_full_decode = lambda p, i: False
    ov = PeakingOverlay(v)
    yield v, ov
    ov.shutdown()
    v.close()


def _show(v, ov, index, qimg):
    v.show_image(index, _pic(f"p{index}"), qimg, object())
    ov.show(index, qimg)


def _item(ov):
    return ov._item


def test_off_in_classic_refuses_and_runs_nothing(rig, monkeypatch):
    v, ov = rig
    calls = []
    monkeypatch.setattr(peaking, "mask_image", lambda *a, **k: calls.append(a))
    ov.set_on(True)  # classic: refused
    assert not ov.is_on()
    _show(v, ov, 0, _checker())
    _settle()
    assert not calls and not _item(ov).isVisible() and ov.mask_for(0) is None


def test_off_means_no_jobs_even_in_advanced(rig, monkeypatch):
    features.set_mode(features.ADVANCED)
    v, ov = rig
    calls = []
    monkeypatch.setattr(peaking, "mask_image", lambda *a, **k: calls.append(a))
    _show(v, ov, 0, _checker())  # never turned on
    _settle()
    assert not calls and not _item(ov).isVisible()


def test_mask_appears_and_lines_up_at_fit_and_after_swap(rig):
    features.set_mode(features.ADVANCED)
    v, ov = rig
    ov.set_on(True)
    assert ov.is_on()
    seen = []
    ov.ready.connect(seen.append)
    t0 = time.monotonic()
    _show(v, ov, 0, _checker())
    assert time.monotonic() - t0 < 1.0  # show() returned; the mask comes later
    assert _pump(lambda: _item(ov).isVisible())
    assert seen == [0]
    base = v.image_item().sceneBoundingRect()
    assert _item(ov).sceneBoundingRect().getRect() == pytest.approx(base.getRect(), abs=0.01)
    assert ov.mask_for(0) is not None and ov.mask_for(0).width() == 200
    # the zoom swap: a larger pixmap replaces the item's; the overlay re-covers it
    v._put(_checker(1000, 750), keep_centre=False)
    QApplication.processEvents()
    assert _item(ov).isVisible()
    assert v.image_item().sceneBoundingRect().width() == 1000
    assert _item(ov).sceneBoundingRect().getRect() == pytest.approx(
        v.image_item().sceneBoundingRect().getRect(), abs=0.01
    )


def test_stale_result_is_dropped(rig, monkeypatch):
    features.set_mode(features.ADVANCED)
    v, ov = rig
    gate = threading.Event()
    real = peaking.mask_image

    def slow(qimg, *a, **k):
        gate.wait(5)
        return real(qimg, *a, **k)

    monkeypatch.setattr(peaking, "mask_image", slow)
    ov.set_on(True)
    seen = []
    ov.ready.connect(seen.append)
    _show(v, ov, 0, _checker())
    _show(v, ov, 1, _checker(300, 200))
    gate.set()
    assert _pump(lambda: 1 in seen)
    _settle()
    assert seen == [1] and ov.mask_for(0) is None and ov.mask_for(1) is not None


def test_moving_on_hides_the_old_mask_until_the_new_one_is_ready(rig, monkeypatch):
    features.set_mode(features.ADVANCED)
    v, ov = rig
    ov.set_on(True)
    _show(v, ov, 0, _checker())
    assert _pump(lambda: _item(ov).isVisible())
    gate = threading.Event()
    real = peaking.mask_image
    monkeypatch.setattr(peaking, "mask_image", lambda q, *a, **k: (gate.wait(5), real(q, *a, **k))[1])
    _show(v, ov, 1, _checker(300, 200))
    QApplication.processEvents()
    assert not _item(ov).isVisible()
    gate.set()
    assert _pump(lambda: _item(ov).isVisible())


def test_cache_is_bounded_and_revisit_is_instant(rig):
    features.set_mode(features.ADVANCED)
    v, ov = rig
    ov.set_on(True)
    for i in range(4):
        _show(v, ov, i, _checker())
        assert _pump(lambda: ov.mask_for(i) is not None)
    assert [i for i in range(4) if ov.mask_for(i) is not None] == [2, 3]
    _show(v, ov, 2, _checker())  # cached: visible with no job
    assert _item(ov).isVisible()


def test_turning_off_hides_and_stops(rig, monkeypatch):
    features.set_mode(features.ADVANCED)
    v, ov = rig
    ov.set_on(True)
    _show(v, ov, 0, _checker())
    assert _pump(lambda: _item(ov).isVisible())
    ov.set_on(False)
    assert not _item(ov).isVisible() and ov.mask_for(0) is None
    calls = []
    monkeypatch.setattr(peaking, "mask_image", lambda *a, **k: calls.append(a))
    _show(v, ov, 1, _checker())
    _settle()
    assert not calls


def test_threshold_change_recomputes(rig):
    features.set_mode(features.ADVANCED)
    v, ov = rig
    ov.set_on(True)
    _show(v, ov, 0, _checker())
    assert _pump(lambda: ov.mask_for(0) is not None)
    first = ov.mask_for(0)
    ov.set_threshold(255)
    assert ov.mask_for(0) is None
    assert _pump(lambda: ov.mask_for(0) is not None)
    assert ov.mask_for(0) is not first


def test_failed_job_leaves_plain_picture(rig, monkeypatch):
    features.set_mode(features.ADVANCED)
    v, ov = rig

    def boom(*a, **k):
        raise ValueError("x")

    monkeypatch.setattr(peaking, "mask_image", boom)
    ov.set_on(True)
    _show(v, ov, 0, _checker())
    _settle()
    assert not _item(ov).isVisible()


def test_none_preview_shows_nothing(rig):
    features.set_mode(features.ADVANCED)
    v, ov = rig
    ov.set_on(True)
    v.show_message("x")
    ov.show(0, None)
    assert not _item(ov).isVisible()


def test_burst_of_shows_starts_no_job_and_the_last_picture_gets_exactly_one(rig, monkeypatch):
    from spuza.ui import peaking_overlay as po

    features.set_mode(features.ADVANCED)
    v, ov = rig
    ov.set_on(True)
    calls = []
    real = peaking.mask_image

    def counting(qimg, *a, **k):
        calls.append(1)
        return real(qimg, *a, **k)

    monkeypatch.setattr(peaking, "mask_image", counting)
    assert ov._settle.isSingleShot() and ov._settle.interval() == po.SETTLE_MS == 150
    for i in range(8):  # a fast walk: each show restarts the settle timer
        _show(v, ov, i, _checker())
    assert ov._settle.isActive() and not calls
    assert _pump(lambda: ov.mask_for(7) is not None)
    _settle()
    assert len(calls) == 1 and ov.mask_for(0) is None  # one job, for the last picture only


def test_cached_mask_shows_immediately_without_the_delay(rig):
    features.set_mode(features.ADVANCED)
    v, ov = rig
    ov.set_on(True)
    img = _checker()
    _show(v, ov, 0, img)
    assert _pump(lambda: _item(ov).isVisible())
    _show(v, ov, 1, _checker())
    assert _pump(lambda: ov.mask_for(1) is not None)
    _show(v, ov, 0, img)  # back to a cached picture
    assert _item(ov).isVisible() and not ov._settle.isActive()


def test_off_and_shutdown_stop_the_settle_timer(rig):
    features.set_mode(features.ADVANCED)
    v, ov = rig
    ov.set_on(True)
    _show(v, ov, 0, _checker())
    assert ov._settle.isActive()
    ov.set_on(False)
    assert not ov._settle.isActive()
    ov.set_on(True)
    _show(v, ov, 0, _checker())
    ov.shutdown()
    assert not ov._settle.isActive()
