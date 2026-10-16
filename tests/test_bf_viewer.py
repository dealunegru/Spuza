"""Bugfinder round 1: viewer zoom on high-DPI, the stale-then-revisit race, filmstrip thumbnail DPR."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from spuza.ui.filmstrip import THUMB_H, THUMB_W, Filmstrip
from spuza.ui.viewer import Viewer

from PySide6.QtCore import QRunnable
import threading
import time


def _img(w=400, h=300) -> QImage:
    im = QImage(w, h, QImage.Format.Format_RGB888)
    im.fill(0x336699)
    return im


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


def _viewer():
    v = Viewer()
    v.resize(800, 600)
    v.show()
    return v


def test_zoom_is_one_image_pixel_per_device_pixel_at_dpr2(monkeypatch):
    monkeypatch.setattr(Viewer, "devicePixelRatioF", lambda self: 2.0)
    v = _viewer()
    v.needs_full_decode = lambda p, i: False
    v.show_image(0, _pic("a"), _img(), object())
    fit = v.transform().m11()
    v.toggle_zoom()
    assert v.transform().m11() == pytest.approx(0.5)  # 1/dpr logical units = 1 device px per image px
    v.toggle_zoom()
    assert v.transform().m11() == pytest.approx(fit)  # fit mode unchanged
    assert v.transform().m11() != pytest.approx(0.5)
    v.shutdown()
    v.close()


def test_zoom_at_dpr1_is_unscaled():
    v = _viewer()
    v.needs_full_decode = lambda p, i: False
    v.show_image(0, _pic("a"), _img(), object())
    v.toggle_zoom()
    assert v.transform().m11() == pytest.approx(1.0)
    v.shutdown()
    v.close()


def test_stale_then_revisit_requeues_decode():
    v = _viewer()
    calls = []
    v.needs_full_decode = lambda p, i: True

    def decode(path, half=False):
        calls.append(str(path))
        from PIL import Image
        return Image.new("RGB", (800, 600), (10, 20, 30))

    v.full_decode = decode
    gate = threading.Event()

    class Block(QRunnable):
        def run(self):
            gate.wait(5)

    v.pool.start(Block())  # holds the single worker so A's job waits in the queue
    A, B = _pic("a"), _pic("b")
    v.zoomed = True
    v.show_image(0, A, _img(), object())  # A: pending
    assert v.full_state(0) == "pending"
    v.show_image(1, B, _img(), object())  # step away; A's job will now see it is stale
    gate.set()
    v.pool.waitForDone(5000)  # A's job ran, emitted "stale" (queued to the GUI thread), B's job too
    v.show_image(0, A, _img(), object())  # back to A before the stale signal is processed
    assert _pump(lambda: v.full_state(0) == "done"), v.full_state(0)
    assert "a" in calls
    v.shutdown()
    v.close()


class _Pf:
    def __init__(self, img):
        self.img = img

    def cached(self, i):
        return self.img


def test_filmstrip_thumb_rendered_at_device_resolution_dpr2(monkeypatch):
    monkeypatch.setattr(Filmstrip, "devicePixelRatioF", lambda self: 2.0)
    class Cat:
        pictures = [_pic("a")]

        def __len__(self):
            return 1

        def is_marked(self, i):
            return False

    fs = Filmstrip(Cat(), _Pf(_img(1040, 620)))
    pm = fs.thumb(0)
    assert pm.devicePixelRatio() == pytest.approx(2.0)
    assert (pm.width(), pm.height()) == (THUMB_W * 2, THUMB_H * 2)
    assert (pm.deviceIndependentSize().width(), pm.deviceIndependentSize().height()) == (THUMB_W, THUMB_H)
    fs.close()
