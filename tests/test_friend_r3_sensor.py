"""Friend round 3: "Zoom shows the full sensor on cropped shots" (default off).

Off: Z on an in-camera-cropped raw shows the camera's frame at native pixels (round 3). On: Z demosaics the whole
visible sensor (no crop) and says "100% · full sensor"; the fitted view stays the frame either way. Uncropped raws
and non-raws behave the same with the row on or off. The cached decodes of the two variants never mix.
Synthetic hooks for the logic; the user's own cropped DNG and an uncropped DNG for the real thing (skipped when absent).
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spuza import preview as pv
from spuza.ui import settings
from spuza.ui.viewer import Viewer, to_qimage

from test_friend_r3_zoom import fake_rawpy, sizes

CROP_DNG = Path("E:/tmp/spuza-test/crop/R0045543.DNG")
JES_DNG = Path("E:/tmp/spuza-test/jes/R0045146.DNG")
GEO = ((100, 50, 400, 300), (800, 600))  # crop frame (left, top, w, h) inside an 800x600 sensor


def pump(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        QTest.qWait(5)
    QApplication.processEvents()
    return cond()


def solid(w, h) -> QImage:
    im = QImage(w, h, QImage.Format.Format_RGB888)
    im.fill(QColor("#336699"))
    return im


def pic_of(path: Path):
    return SimpleNamespace(primary=path, stem=path.stem)


def make_viewer(geo=GEO, on=True):
    """A viewer whose hooks are fakes: the frame is 400x300, the sensor 800x600; calls are recorded."""
    v = Viewer()
    v.resize(500, 400)
    v.show()
    v.calls = []
    v.sensor_geometry = lambda p: v.calls.append("geometry") or geo
    v.sensor_decode = lambda p: v.calls.append("sensor") or Image.new("RGB", (800, 600), (200, 20, 20))
    v.needs_full_decode = lambda p, i: False
    v.native_decode = lambda p: v.calls.append("native") or Image.new("RGB", (400, 300), (20, 20, 200))
    v.zoom_full_sensor = on
    return v


def show(v, path, size=(400, 300), index=0):
    info = SimpleNamespace(width=size[0], height=size[1], source="fake", focus=None)
    v.show_image(index, pic_of(path), solid(*size), info)
    return info


def test_row_on_zoom_decodes_the_whole_sensor_and_says_so():
    v = make_viewer()
    show(v, Path("a.dng"))
    assert v.image_size() == (400, 300)
    v.toggle_zoom()
    assert pump(lambda: v.full_state(0) == "done")
    assert v.calls == ["geometry", "sensor"] and v.image_size() == (800, 600)
    assert v.zoom_text() == "100% · full sensor" and v.hud_visible and v.sensor_shown
    v.toggle_zoom()  # the fitted view is the camera's frame again
    assert v.image_size() == (400, 300) and not v.sensor_shown and v.zoom_text() == ""
    v.shutdown()
    v.close()


def test_row_off_zoom_is_the_frame_and_never_asks_about_the_sensor():
    v = make_viewer(on=False)
    show(v, Path("a.dng"))
    v.toggle_zoom()
    assert pump(lambda: v.full_state(0) == "skipped")
    assert v.calls == [] and v.image_size() == (400, 300)
    assert v.zoom_text() == "100%" and not v.sensor_shown
    v.shutdown()
    v.close()


def test_uncropped_raw_behaves_the_same_with_the_row_on_or_off():
    seen = []
    for on in (True, False):
        v = make_viewer(geo=None, on=on)
        show(v, Path("u.dng"), size=(300, 200))
        v.toggle_zoom()
        assert pump(lambda: v.full_state(0) in ("skipped", "done"))
        seen.append((v.calls.count("sensor"), v.image_size(), v.zoom_text(), v.sensor_shown))
        v.shutdown()
        v.close()
    assert seen[0] == seen[1] == (0, (300, 200), "100%", False)


def test_non_raw_ignores_the_row(tmp_path):
    p = tmp_path / "x.jpg"
    Image.new("RGB", (900, 600), (30, 90, 160)).save(p, "JPEG")
    capped, info = pv.load(p, 450)
    v = Viewer()
    v.resize(500, 400)
    v.show()
    v.zoom_full_sensor = True  # the real hooks: a JPEG has no sensor geometry
    assert pv.sensor_geometry(p) is None
    v.show_image(0, pic_of(p), to_qimage(capped), info)
    v.toggle_zoom()
    assert pump(lambda: v.full_state(0) == "done")
    assert v.image_size() == (900, 600) and v.zoom_text() == "100%" and not v.sensor_shown
    v.shutdown()
    v.close()


def test_cache_keeps_the_two_variants_apart():
    v = make_viewer()
    path = Path("a.dng")
    show(v, path)
    v.toggle_zoom()
    assert pump(lambda: v.sensor_shown)
    v.set_zoom_full_sensor(False)  # flipped while Z is on: the frame, not the cached sensor
    assert pump(lambda: v.full_state(0) in ("skipped", "done") and not v.sensor_shown)
    assert v.image_size() == (400, 300) and v.zoom_text() == "100%"
    keys = set(v._full_cache)
    assert (str(path), "sensor") in keys
    v.set_zoom_full_sensor(True)  # back: the cached sensor, no second decode
    assert v.sensor_shown and v.image_size() == (800, 600) and v.calls.count("sensor") == 1
    v.shutdown()
    v.close()


def test_cache_never_shows_a_cached_frame_as_the_sensor():
    v = make_viewer(on=False)
    path = Path("a.dng")
    v.native_decode = lambda p: Image.new("RGB", (400, 300), (1, 2, 3))
    info = SimpleNamespace(width=400, height=300, source="fake", focus=None)
    v.show_image(0, pic_of(path), solid(200, 150), info)  # a capped preview: Z fetches the native frame and caches it
    v.toggle_zoom()
    assert pump(lambda: v.full_state(0) == "done")
    assert (str(path), "frame") in v._full_cache and not v.sensor_shown
    v.set_zoom_full_sensor(True)
    assert pump(lambda: v.sensor_shown)
    assert v.image_size() == (800, 600) and v.zoom_text() == "100% · full sensor"
    v.shutdown()
    v.close()


def test_centre_of_the_frame_maps_to_the_same_subject_in_the_sensor():
    v = make_viewer()
    show(v, Path("a.dng"))
    v.toggle_zoom()
    assert pump(lambda: v.sensor_shown)
    fx, fy = v._centre_frac()  # frame centre (200, 150) + origin (100, 50) = (300, 200) of 800x600
    assert (fx, fy) == pytest.approx((300 / 800, 200 / 600), abs=0.01)
    v.shutdown()
    v.close()


def test_target_point_maps_the_same_way_and_back_again():
    v = make_viewer()
    v.resize(200, 150)  # smaller than the frame, so the target point can really be centred
    v.zoom_target = lambda: (0.6, 0.6)  # x = 100 + 0.6 * 400 = 340 of 800, y = 50 + 0.6 * 300 = 230 of 600
    show(v, Path("a.dng"))
    v.toggle_zoom()
    assert pump(lambda: v.sensor_shown)
    assert v._centre_frac() == pytest.approx((340 / 800, 230 / 600), abs=0.01)
    back = Viewer._map_frac((340 / 800, 230 / 600), GEO, to_sensor=False)  # and the same point back into the frame
    assert back == pytest.approx((0.6, 0.6)) and Viewer._map_frac((0.0, 0.0), GEO, to_sensor=False) == (0.0, 0.0)
    v.shutdown()
    v.close()


def test_failed_sensor_decode_says_so():
    v = make_viewer()
    v.sensor_decode = lambda p: (_ for _ in ()).throw(OSError("gone"))
    show(v, Path("a.dng"))
    v.toggle_zoom()
    assert pump(lambda: v.full_state(0) == "error")
    assert v.zoom_text() == "preview pixels only (full decode failed)" and not v.sensor_shown
    v.shutdown()
    v.close()


def test_pending_decode_survives_a_toggle_flip_and_ends_on_the_variant_asked_for():
    v = make_viewer()
    show(v, Path("a.dng"))
    v.toggle_zoom()
    v.set_zoom_full_sensor(False)  # before the worker answers
    assert pump(lambda: v.full_state(0) in ("done", "skipped") and not v.sensor_shown and v.zoom_text() == "100%")
    assert v.image_size() == (400, 300)
    v.shutdown()
    v.close()


# ---- geometry (rawpy stand-in) -----------------------------------------------------------------------

def test_sensor_geometry_for_cropped_uncropped_and_portrait(monkeypatch, tmp_path):
    p = tmp_path / "c.dng"
    p.write_bytes(b"x")
    fake_rawpy(monkeypatch, sizes(crop_width=3000, crop_height=2000, crop_left_margin=1568, crop_top_margin=1040))
    assert pv.sensor_geometry(p) == ((1510, 1012, 3000, 2000), (6020, 4024))
    assert pv.full_decode(p, crop=False).size == (6020, 4024) and pv.full_decode(p).size == (3000, 2000)
    fake_rawpy(monkeypatch, sizes(flip=6, crop_width=3000, crop_height=2000, crop_left_margin=1568, crop_top_margin=1040))
    assert pv.sensor_geometry(p)[1] == (4024, 6020) and pv.full_decode(p, crop=False).size == (4024, 6020)
    for s in (sizes(crop_width=0, crop_height=0), sizes(crop_left_margin=58, crop_top_margin=28, crop_width=6020, crop_height=4024)):
        fake_rawpy(monkeypatch, s)
        assert pv.sensor_geometry(p) is None  # no crop, or a crop as big as the sensor


def test_sensor_geometry_is_none_when_rawpy_is_missing_or_the_file_is_not_a_raw(monkeypatch, tmp_path):
    p = tmp_path / "c.dng"
    p.write_bytes(b"x")
    monkeypatch.setitem(__import__("sys").modules, "rawpy", None)
    assert pv.sensor_geometry(p) is None
    assert pv.sensor_geometry(tmp_path / "x.jpg") is None


# ---- the preference: settings, sheet, window ---------------------------------------------------------

def test_settings_round_trip_and_default_off():
    s = settings.open_settings()
    assert settings.read_zoom_full_sensor(s) is False
    settings.write_zoom_full_sensor(s, True)
    assert settings.read_zoom_full_sensor(settings.open_settings()) is True
    settings.write_zoom_full_sensor(s, False)
    assert settings.read_zoom_full_sensor(settings.open_settings()) is False


def test_prefs_sheet_row_seed_and_reset():
    from spuza.ui.prefs_sheet import PrefsSheet

    sheet = PrefsSheet(5, 3)
    assert sheet.sensor_check.text() == "Zoom shows the full sensor on cropped shots"
    assert sheet.zoom_full_sensor() is False and sheet.seeded_sensor is False
    sheet = PrefsSheet(5, 3, zoom_full_sensor=True)
    assert sheet.zoom_full_sensor() is True and sheet.seeded_sensor is True
    sheet.reset_to_defaults()
    assert sheet.zoom_full_sensor() is False


def test_window_seeds_the_viewer_from_the_saved_value():
    from test_friend_r3_window import make_window

    settings.write_zoom_full_sensor(settings.open_settings(), True)
    w = make_window()
    try:
        assert w.viewer.zoom_full_sensor is True
        assert w._default_prefs_sheet_factory(5, 3, 2560).zoom_full_sensor() is True
    finally:
        w.close()


def test_window_saves_only_when_flipped_and_applies_at_once():
    from test_friend_r3_window import make_window

    w = make_window()
    try:
        assert w.viewer.zoom_full_sensor is False
        w.open_prefs()
        w._prefs_sheet.accept()
        assert "zoom/full_sensor" not in settings.open_settings().allKeys()  # an unrelated OK writes nothing
        w.open_prefs()
        w._prefs_sheet.sensor_check.setChecked(True)
        w._prefs_sheet.accept()
        assert settings.read_zoom_full_sensor(settings.open_settings()) is True
        assert w.viewer.zoom_full_sensor is True
    finally:
        w.close()


# ---- the real files (skipped when absent) -------------------------------------------------------------

def _gray_small(im: Image.Image, size=(48, 32)):
    import numpy as np

    return np.asarray(im.convert("L").resize(size, Image.BILINEAR), dtype="float64").ravel()


@pytest.mark.skipif(not CROP_DNG.exists(), reason="needs the in-camera-crop DNG")
def test_real_cropped_dng_row_off_is_the_frame_and_row_on_is_the_sensor(capsys):
    pytest.importorskip("rawpy")
    import numpy as np

    capped, info = pv.load(CROP_DNG)
    assert (info.width, info.height) == (3360, 2240)
    geo = pv.sensor_geometry(CROP_DNG)
    assert geo is not None
    box, (sw, sh) = geo
    assert box[2:] == (3360, 2240) and (sw, sh) == (6020, 4024)

    off = Viewer()
    off.resize(800, 600)
    off.show()
    called = []
    off.full_decode = lambda *a, **k: called.append("rawpy")
    off.show_image(0, pic_of(CROP_DNG), to_qimage(capped), info)
    off.toggle_zoom()
    assert pump(lambda: off.full_state(0) == "skipped") and not called
    print("row off:", off.image_size(), off.zoom_text())
    assert off.image_size() == (3360, 2240) and off.zoom_text() == "100%"
    off.shutdown()
    off.close()

    on = Viewer()
    on.resize(800, 600)
    on.show()
    on.zoom_full_sensor = True
    on.show_image(0, pic_of(CROP_DNG), to_qimage(capped), info)
    t0 = time.perf_counter()
    on.toggle_zoom()
    assert pump(lambda: on.sensor_shown, timeout=60)
    ms = (time.perf_counter() - t0) * 1000
    print(f"row on: {on.image_size()} {on.zoom_text()!r} time-to-native {ms:.0f} ms")
    assert on.image_size() == (sw, sh) and on.zoom_text() == "100% · full sensor"
    fx, fy = on._centre_frac()
    assert (fx, fy) == pytest.approx(((box[0] + box[2] / 2) / sw, (box[1] + box[3] / 2) / sh), abs=0.002)

    # the crop frame cut out of the whole sensor is the preview: same subject at the mapped place
    full = pv.full_decode(CROP_DNG, crop=False)
    region = full.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
    a, b = _gray_small(region), _gray_small(capped)
    r_in = float(np.corrcoef(a, b)[0, 1])
    # the camera's tone curve is not rawpy's, so the correlation is modest; what counts is that the mapped
    # place beats the same region shifted by 80 px (a bit under 2.5% of the frame) in any direction
    shifted = {}
    for dx in (-80, 0, 80):
        for dy in (-80, 0, 80):
            r = full.crop((box[0] + dx, box[1] + dy, box[0] + dx + box[2], box[1] + dy + box[3]))
            shifted[(dx, dy)] = float(np.corrcoef(_gray_small(r), b)[0, 1])
    print(f"centre mapping: corr at the mapped place {r_in:.3f}; shifted +-80 px: "
          f"{ {k: round(x, 3) for k, x in shifted.items() if k != (0, 0)} }")
    assert max(shifted, key=shifted.get) == (0, 0) and r_in > 0.3
    on.shutdown()
    on.close()


@pytest.mark.skipif(not JES_DNG.exists(), reason="needs the uncropped DNG")
def test_real_uncropped_dng_is_identical_with_the_row_on_or_off(capsys):
    pytest.importorskip("rawpy")
    capped, info = pv.load(JES_DNG)
    assert pv.sensor_geometry(JES_DNG) is None
    out = []
    for on in (False, True):
        v = Viewer()
        v.resize(800, 600)
        v.show()
        v.zoom_full_sensor = on
        v.show_image(0, pic_of(JES_DNG), to_qimage(capped), info)
        v.toggle_zoom()
        assert pump(lambda: v.full_state(0) in ("done", "skipped"), timeout=60)
        out.append((v.image_size(), v.zoom_text(), v.sensor_shown))
        v.shutdown()
        v.close()
    print("uncropped off/on:", out)
    assert out[0] == out[1] and out[0][1] == "100%" and out[0][2] is False
