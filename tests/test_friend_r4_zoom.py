"""Friend round 4: when a picture has a JPEG sidecar, Z shows the sidecar's own pixels at native size (viewer,
review grid, compare) and never demosaics the raw. A .hif sidecar does not count; an unreadable JPEG falls back
to the old rule. Synthetic pairs under tmp_path; the real pair is skip-if-absent."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza import features, preview as pv
from spuza.catalog import Catalog, Picture
from spuza.ui.compare import CompareView
from spuza.ui.review import ReviewGrid
from spuza.ui.viewer import Viewer

REAL_PAIR = Path("E:/tmp/spuza-test/pair")


def pump(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        QTest.qWait(5)
    QApplication.processEvents()
    return cond()


def solid(w=64, h=48) -> QImage:
    im = QImage(w, h, QImage.Format.Format_RGB888)
    im.fill(QColor("#336699"))
    return im


def make_pair(tmp_path, size=(300, 200), orientation=None, sidecar_ext=".jpg"):
    raw = tmp_path / "P1.RAF"
    raw.write_bytes(b"not a real raw")
    side = tmp_path / ("P1" + sidecar_ext)
    im = Image.new("RGB", size, (30, 90, 160))
    if orientation:
        ex = Image.Exif()
        ex[0x0112] = orientation
        im.save(side, "JPEG", exif=ex)
    else:
        im.save(side, "JPEG")
    return Picture("P1", raw, side, (raw, side), "RAF+JPG", 1, 1.0)


def viewer(raw_calls):
    v = Viewer()
    v.resize(800, 600)
    v.show()
    v.needs_full_decode = lambda p, i: True  # the X-Pro2 case: a 1920 px preview against a 6000 px frame
    v.full_decode = lambda p, half=False: raw_calls.append("rawpy") or Image.new("RGB", (900, 600))
    v.sensor_geometry = lambda p: None
    v.sensor_decode = lambda p: raw_calls.append("sensor") or Image.new("RGB", (950, 650))
    return v


def zoom_viewer(pic, v):
    v.show_image(0, pic, solid(), SimpleNamespace(width=192, height=128))
    v.toggle_zoom()
    assert pump(lambda: v.full_state(0) in ("done", "error"))


def test_viewer_z_shows_the_sidecar_at_native_size_without_rawpy(tmp_path):
    pic = make_pair(tmp_path)
    calls = []
    v = viewer(calls)
    zoom_viewer(pic, v)
    assert v.full_state(0) == "done" and v.image_size() == (300, 200)
    assert calls == [] and v.zoom_text() == "100%"
    assert list(v._full_cache) == [(str(pic.primary), "frame")]  # keyed by the primary
    v.shutdown()
    v.close()


def test_viewer_honours_exif_orientation_of_the_sidecar(tmp_path):
    pic = make_pair(tmp_path, orientation=6)
    calls = []
    v = viewer(calls)
    zoom_viewer(pic, v)
    assert v.image_size() == (200, 300) and calls == []
    v.shutdown()
    v.close()


def test_viewer_user_rotation_applies_on_top_of_the_sidecar(tmp_path):
    pic = make_pair(tmp_path)
    v = viewer([])
    v.rotation_of = lambda i: 1
    zoom_viewer(pic, v)
    assert v.image_size() == (200, 300)
    v.shutdown()
    v.close()


def test_viewer_unreadable_sidecar_falls_back_to_the_raw_rule_and_says_so(tmp_path):
    pic = make_pair(tmp_path)
    pic.sidecar.write_bytes(b"garbage, not a jpeg")
    calls = []
    v = viewer(calls)
    zoom_viewer(pic, v)
    assert calls == ["rawpy"] and v.image_size() == (900, 600)
    assert "JPG unreadable" in v.zoom_text() and v.hud.isVisibleTo(v)
    v.shutdown()
    v.close()


def test_viewer_hif_sidecar_does_not_count(tmp_path):
    pic = make_pair(tmp_path, sidecar_ext=".hif")
    calls = []
    v = viewer(calls)
    zoom_viewer(pic, v)
    assert calls == ["rawpy"] and v.zoom_text() == "100%"
    v.shutdown()
    v.close()


def test_viewer_without_sidecar_is_unchanged(tmp_path):
    pic = make_pair(tmp_path)
    pic = SimpleNamespace(primary=pic.primary, sidecar=None)
    calls = []
    v = viewer(calls)
    zoom_viewer(pic, v)
    assert calls == ["rawpy"] and v.image_size() == (900, 600)
    v.shutdown()
    v.close()


def test_full_sensor_preference_still_wins_on_a_cropped_raw_with_a_sidecar(tmp_path):
    pic = make_pair(tmp_path)
    calls = []
    v = viewer(calls)
    v.zoom_full_sensor = True
    v.sensor_geometry = lambda p: ((10, 10, 100, 100), (950, 650))
    zoom_viewer(pic, v)
    assert calls == ["sensor"] and v.sensor_shown and v.zoom_text() == "100% · full sensor"
    v.shutdown()
    v.close()


def test_full_sensor_preference_on_an_uncropped_raw_uses_the_sidecar(tmp_path):
    pic = make_pair(tmp_path)
    calls = []
    v = viewer(calls)
    v.zoom_full_sensor = True
    zoom_viewer(pic, v)
    assert calls == [] and v.image_size() == (300, 200) and not v.sensor_shown
    assert v.zoom_text() == "100%"
    v.shutdown()
    v.close()


def _boom(*a, **k):
    raise AssertionError("the raw must not be read")


def test_load_native_takes_the_sidecar_and_never_touches_the_raw(tmp_path, monkeypatch):
    pic = make_pair(tmp_path, orientation=6)
    monkeypatch.setattr(pv, "load", _boom)
    monkeypatch.setattr(pv, "_rawpy_full", _boom)
    im, info = pv.load_native(pic.primary, pic.sidecar)
    assert im.size == (200, 300) and info.native is True and info.source == "jpeg-sidecar"
    assert (info.width, info.height) == (200, 300) and info.path == pic.primary


def test_load_native_unreadable_or_hif_sidecar_uses_the_old_rule(tmp_path, monkeypatch):
    pic = make_pair(tmp_path)
    pic.sidecar.write_bytes(b"junk")
    seen = []
    info = pv.PreviewInfo(pic.primary, "raf-embedded", 192, 128, 1, 0.0)
    monkeypatch.setattr(pv, "load", lambda p, m=2560: seen.append("load") or (Image.new("RGB", (192, 128)), info))
    monkeypatch.setattr(pv, "needs_full_decode", lambda p, i: True)
    monkeypatch.setattr(pv, "_rawpy_full",
                        lambda p, half, crop=True: seen.append("rawpy") or Image.new("RGB", (900, 600)))
    im, got = pv.load_native(pic.primary, pic.sidecar)
    assert seen == ["load", "rawpy"] and got.source == "rawpy-full" and im.size == (900, 600)
    seen.clear()
    pv.load_native(pic.primary, tmp_path / "P1.hif")  # not a JPEG sidecar: ignored
    assert seen == ["load", "rawpy"]
    seen.clear()
    pv.load_native(pic.primary, None)
    assert seen == ["load", "rawpy"]


def test_sidecar_native_rejects_junk_cleanly(tmp_path):
    for name, data in (("e.jpg", b""), ("t.jpg", b"\xff\xd8\xff" + b"\x00" * 10), ("n.png", b"x")):
        f = tmp_path / name
        f.write_bytes(data)
        with pytest.raises(pv.PreviewError):
            pv.sidecar_native(f)
    with pytest.raises(pv.PreviewError):
        pv.sidecar_native(tmp_path / "missing.jpg")


def test_is_jpeg_sidecar_is_strict():
    assert pv.is_jpeg_sidecar(Path("a.JPG")) and pv.is_jpeg_sidecar("a.jpeg")
    assert not pv.is_jpeg_sidecar(Path("a.hif")) and not pv.is_jpeg_sidecar(None) and not pv.is_jpeg_sidecar(7)


def _no_raw(monkeypatch):
    def load(path, max_side=2560):
        if max_side is None:
            raise AssertionError("native load went to the raw")
        return Image.new("RGB", (64, 43)), pv.PreviewInfo(Path(path), "raf-embedded", 192, 128, 1, 0.0)

    monkeypatch.setattr(pv, "load", load)
    monkeypatch.setattr(pv, "_rawpy_full", _boom)


def test_review_grid_z_uses_the_sidecar(tmp_path, monkeypatch):
    pic = make_pair(tmp_path)
    _no_raw(monkeypatch)
    g = ReviewGrid(Catalog([pic]), SimpleNamespace(cached=lambda i: solid()))
    g.resize(900, 600)
    g.show()
    g._zoom_index = 0
    g._zoom_fit = False
    g._want_native()
    assert pump(lambda: g._native_img is not None)
    assert (g._native_img[1].width(), g._native_img[1].height()) == (300, 200)
    g.wait_idle()
    g.close()


def test_review_native_loader_passes_the_sidecar(tmp_path, monkeypatch):
    pic = make_pair(tmp_path)
    _no_raw(monkeypatch)
    g = ReviewGrid(Catalog([pic]), SimpleNamespace(cached=lambda i: solid()))
    im, info = g._native_loader(pic.sidecar)(pic.primary, None)
    assert im.size == (300, 200) and info.source == "jpeg-sidecar"
    g.wait_idle()
    g.close()


def test_compare_zoom_loader_passes_the_sidecar(tmp_path, monkeypatch):
    pic = make_pair(tmp_path)
    _no_raw(monkeypatch)
    features.set_mode(features.ADVANCED)
    try:
        view = CompareView(Catalog([pic, pic]), SimpleNamespace(cached=lambda i: solid(), info=lambda i: None))
        im, info = view._zoom_loader(pic.sidecar)(pic.primary, None)
        assert im.size == (300, 200) and info.native is True and info.source == "jpeg-sidecar"
        view.shutdown()
        view.close()
    finally:
        features.set_mode(features.CLASSIC)


@pytest.mark.skipif(not (REAL_PAIR / "20250704_X-Pro2_9268.JPG").exists(), reason="needs the real pair")
def test_real_pair_zooms_to_the_6000_px_jpeg_without_rawpy(monkeypatch):
    jpg = REAL_PAIR / "20250704_X-Pro2_9268.JPG"
    raf = REAL_PAIR / "20250704_X-Pro2_9268.RAF"
    rawpy = pytest.importorskip("rawpy")
    monkeypatch.setattr(rawpy, "imread", _boom)
    im, info = pv.load_native(raf, jpg)
    assert im.size == (6000, 4000) and info.source == "jpeg-sidecar" and info.native is True
