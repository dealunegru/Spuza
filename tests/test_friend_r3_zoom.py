"""Friend round 3: Z shows the native pixels (viewer, review grid, compare), the camera's frame is the picture,
and a user rotation is shown at display time. Synthetic data under tmp_path; the jes set is skip-if-absent."""

from __future__ import annotations

import shutil
import subprocess
import sys
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
from spuza.ui.viewer import Viewer, to_qimage

from conftest import DNG_NAMES, REAL_SET


def pump(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        QTest.qWait(5)
    QApplication.processEvents()
    return cond()


def solid(w=400, h=300) -> QImage:
    im = QImage(w, h, QImage.Format.Format_RGB888)
    im.fill(QColor("#336699"))
    return im


def make_jpeg(path: Path, size=(3000, 2000)) -> Path:
    Image.new("RGB", size, (30, 90, 160)).save(path, "JPEG", quality=70)
    return path


def viewer():
    v = Viewer()
    v.resize(800, 600)
    v.show()
    return v


def pic_of(path: Path):
    return SimpleNamespace(primary=path, stem=path.stem)


# ---- native pixels in the viewer ---------------------------------------------------------------------

def test_capped_preview_of_full_size_jpeg_swaps_to_native_on_z(tmp_path):
    path = make_jpeg(tmp_path / "big.jpg")
    capped, info = pv.load(path, 1000)  # what the fitted view holds: a draft-scaled decode
    assert max(capped.size) < 3000 and (info.width, info.height) == (3000, 2000)
    v = viewer()
    v.show_image(0, pic_of(path), to_qimage(capped), info)
    v.toggle_zoom()
    assert v.zoom_text().startswith("100% …")  # not "100%" while the native pixels load
    assert pump(lambda: v.full_state(0) == "done")
    assert v.image_size() == (3000, 2000)
    assert v.zoom_text() == "100%"
    v.shutdown()
    v.close()


def test_raw_with_small_preview_still_goes_to_rawpy(tmp_path):
    path = tmp_path / "x.dng"
    path.write_bytes(b"x")
    calls = []
    v = viewer()
    v.needs_full_decode = lambda p, i: True
    v.full_decode = lambda p, half=False: calls.append("rawpy") or Image.new("RGB", (900, 600))
    v.native_decode = lambda p: calls.append("native") or Image.new("RGB", (10, 10))
    v.show_image(0, pic_of(path), solid(), SimpleNamespace(width=400, height=300))
    v.toggle_zoom()
    assert pump(lambda: v.full_state(0) == "done")
    assert calls == ["rawpy"] and v.image_size() == (900, 600)
    v.shutdown()
    v.close()


def test_jpeg_only_picture_zooms_to_native_size(tmp_path):
    path = make_jpeg(tmp_path / "plain.jpg", (5200, 3400))
    capped, info = pv.load(path)  # the default 2560 cap
    assert max(capped.size) < 5200
    v = viewer()
    v.show_image(0, pic_of(path), to_qimage(capped), info)
    v.toggle_zoom()
    assert pump(lambda: v.image_size() == (5200, 3400))
    assert v.zoom_text() == "100%"
    v.shutdown()
    v.close()


def test_preview_already_native_is_not_decoded_again(tmp_path):
    path = make_jpeg(tmp_path / "small.jpg", (400, 300))
    im, info = pv.load(path)
    calls = []
    v = viewer()
    v.native_decode = lambda p: calls.append(p)
    v.show_image(0, pic_of(path), to_qimage(im), info)
    v.toggle_zoom()
    assert pump(lambda: v.full_state(0) == "skipped")
    assert not calls and v.image_size() == (400, 300) and v.zoom_text() == "100%"
    v.shutdown()
    v.close()


def test_failed_native_decode_says_so(tmp_path):
    path = make_jpeg(tmp_path / "big.jpg")
    capped, info = pv.load(path, 1000)
    v = viewer()

    def boom(p):
        raise OSError("disk went away")

    v.native_decode = boom
    v.show_image(0, pic_of(path), to_qimage(capped), info)
    v.toggle_zoom()
    assert pump(lambda: v.full_state(0) == "error")
    assert v.zoom_text().startswith("preview pixels only")
    v.shutdown()
    v.close()


def test_is_capped_is_defensive():
    assert pv.is_capped((1000, 700), SimpleNamespace(width=3000, height=2000))
    assert not pv.is_capped((3000, 2000), SimpleNamespace(width=3000, height=2000))
    for bad in (None, object(), SimpleNamespace(width="x", height=None), SimpleNamespace(width=float("nan"), height=1)):
        assert pv.is_capped((10, 10), bad) is False


# ---- the camera's frame (crop) -----------------------------------------------------------------------

def sizes(**kw):
    base = dict(raw_height=4064, raw_width=6112, height=4024, width=6020, top_margin=28, left_margin=58,
                flip=0, crop_left_margin=68, crop_top_margin=40, crop_width=6000, crop_height=4000)
    base.update(kw)
    return SimpleNamespace(**base)


def test_frame_box_follows_the_flip_and_refuses_nonsense():
    assert pv._frame_box(sizes()) == (10, 12, 6000, 4000)
    assert pv._frame_box(sizes(flip=6)) == (4024 - (12 + 4000), 10, 4000, 6000)
    assert pv._frame_box(sizes(flip=5)) == (12, 6020 - (10 + 6000), 4000, 6000)
    assert pv._frame_box(sizes(flip=3)) == (6020 - 6010, 4024 - 4012, 6000, 4000)
    for bad in (sizes(crop_width=0), sizes(crop_width=9000), sizes(crop_left_margin=0), SimpleNamespace()):
        assert pv._frame_box(bad) is None


class FakeRaw:
    """A rawpy stand-in: the 'visible area' array carries a marker pixel at the crop origin."""

    def __init__(self, s, half=False):
        self.sizes, self.half = s, half

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def postprocess(self, use_camera_wb=True, half_size=False):
        import numpy as np

        h, w = (self.sizes.width, self.sizes.height) if self.sizes.flip in (5, 6) else (self.sizes.height, self.sizes.width)
        if half_size:
            h, w = h // 2, w // 2
        return np.zeros((h, w, 3), dtype="uint8")


def fake_rawpy(monkeypatch, s):
    mod = SimpleNamespace(imread=lambda p: FakeRaw(s))
    monkeypatch.setitem(sys.modules, "rawpy", mod)


def test_cropped_shot_is_judged_against_the_crop_and_demosaiced_cropped(monkeypatch, tmp_path):
    s = sizes(crop_width=3000, crop_height=2000, crop_left_margin=1568, crop_top_margin=1040)  # a crop mode
    fake_rawpy(monkeypatch, s)
    path = tmp_path / "c.dng"
    path.write_bytes(b"x")
    assert pv.frame_size(path) == (3000, 2000)
    full_preview = SimpleNamespace(width=3000, height=2000)  # the camera wrote the cropped frame as its preview
    small_preview = SimpleNamespace(width=1200, height=800)
    assert pv.needs_full_decode(path, full_preview) is False  # against the sensor (6020) it would be True
    assert pv.needs_full_decode(path, small_preview) is True
    im = pv.full_decode(path)
    assert im.size == (3000, 2000)  # never the whole 6020x4024 sensor
    assert im.size[0] / im.size[1] == pytest.approx(3000 / 2000)  # same aspect as the fitted preview
    half = pv.full_decode(path, half_size=True)
    assert half.size == (1500, 1000)


def test_cropped_portrait_frame_after_flip(monkeypatch, tmp_path):
    fake_rawpy(monkeypatch, sizes(flip=6, crop_width=3000, crop_height=2000, crop_left_margin=1568, crop_top_margin=1040))
    path = tmp_path / "p.dng"
    path.write_bytes(b"x")
    assert pv.frame_size(path) == (2000, 3000)
    assert pv.full_decode(path).size == (2000, 3000)


def test_uncropped_or_missing_crop_uses_the_visible_sensor(monkeypatch, tmp_path):
    fake_rawpy(monkeypatch, sizes(crop_width=0, crop_height=0))
    path = tmp_path / "u.dng"
    path.write_bytes(b"x")
    assert pv.frame_size(path) == (6020, 4024)
    assert pv.full_decode(path).size == (6020, 4024)


def exiftool():
    return shutil.which("exiftool")


@pytest.mark.skipif(not (REAL_SET / DNG_NAMES[0]).exists() or exiftool() is None, reason="needs the jes set + exiftool")
def test_real_dng_with_a_crop_mode_zooms_to_the_crop_frame(tmp_path):
    pytest.importorskip("rawpy")
    dst = tmp_path / "crop.DNG"
    shutil.copy(REAL_SET / DNG_NAMES[0], dst)
    subprocess.run([exiftool(), "-overwrite_original", "-DefaultCropOrigin=1500 1000", "-DefaultCropSize=3000 2000",
                    str(dst)], check=True, capture_output=True, timeout=60)
    assert pv.frame_size(dst) == (3000, 2000)
    _im, info = pv.load(dst)
    assert pv.needs_full_decode(dst, info) is False  # the 6000 px preview is bigger than the 3000 px frame
    im, info = pv.load_native(dst)
    assert im.size == (6000, 4000) and info.native is True  # the embedded preview, uncapped (jes previews are uncropped)
    small = SimpleNamespace(width=1200, height=800)
    assert pv.needs_full_decode(dst, small) is True
    assert pv.full_decode(dst).size == (3000, 2000)


# ---- rotation shown at display time ------------------------------------------------------------------

def test_viewer_rotation_swaps_shown_size_and_repaints_keeping_zoom():
    q = {0: 0}
    v = viewer()
    v.rotation_of = lambda i: q.get(i, 0)
    v.needs_full_decode = lambda p, i: False
    v.show_image(0, pic_of(Path("a.jpg")), solid(400, 300), SimpleNamespace(width=400, height=300))
    assert v.image_size() == (400, 300)
    v.toggle_zoom()
    pump(lambda: v.full_state(0) == "skipped")
    scale = v.transform().m11()
    v.centerOn(v.image_item().mapToScene(100, 50))
    before = v._centre_frac()
    q[0] = 1
    v.rotation_changed(0)
    assert v.image_size() == (300, 400) and v.zoomed
    assert v.image_item().sceneBoundingRect().width() == pytest.approx(300)
    assert v.transform().m11() == pytest.approx(scale)
    after = v._centre_frac()
    assert after[0] == pytest.approx(before[0], abs=0.02) and after[1] == pytest.approx(before[1], abs=0.02)
    v.rotation_changed(5)  # a picture that is not shown: nothing moves
    assert v.image_size() == (300, 400)
    q[0] = 0
    v.rotation_changed(0)
    assert v.image_size() == (400, 300)
    v.shutdown()
    v.close()


def test_viewer_fitted_rotation_fits_the_turned_picture():
    v = viewer()
    v.rotation_of = lambda i: 1
    v.show_image(0, pic_of(Path("a.jpg")), solid(400, 300), None)
    # 300x400 turned picture fitted into 800x600: the height limits it
    assert v.transform().m11() == pytest.approx(600 / 400, rel=0.02)
    v.shutdown()
    v.close()


def test_viewer_overlays_turn_with_the_item():
    """Peaking mask and AF ring are children of the item (item coordinates): they stay on the same picture point."""
    v = viewer()
    v.rotation_of = lambda i: 1
    v.show_image(0, pic_of(Path("a.jpg")), solid(400, 300), None)
    from PySide6.QtCore import QPointF

    sp = v.image_item().mapToScene(QPointF(100, 50))  # a point 100 right and 50 down in the 400x300 picture
    # turned 90 degrees clockwise about the centre (200, 150): it lands 100 left of the centre column and up
    assert (sp.x(), sp.y()) == pytest.approx((300.0, 50.0))
    v.shutdown()
    v.close()


class Cat3:
    def __init__(self, n=3):
        self.pictures = [Picture(f"P{i}", Path(f"/x/P{i}.JPG"), None, (Path(f"/x/P{i}.JPG"),), "JPG", 1, 1.0)
                         for i in range(n)]
        self.marked = set()

    def __len__(self):
        return len(self.pictures)

    def is_marked(self, i):
        return i in self.marked

    def set_mark(self, i, on):
        (self.marked.add if on else self.marked.discard)(i)

    marked_count = 0
    marked_bytes = 0


def test_review_thumbnail_and_zoom_page_follow_rotation():
    cat = Catalog(Cat3().pictures)
    cat.set_mark(1, True)
    calls = []

    def loader(path, max_side):
        calls.append(max_side)
        return Image.new("RGB", (600, 400) if max_side is None else (60, 40)), SimpleNamespace(native=True)

    class Pf:
        def cached(self, i):
            return solid(64, 48)

    g = ReviewGrid(cat, Pf(), loader=loader)
    g.resize(900, 600)
    g.show()
    q = {1: 0}
    g.rotation_of = lambda i: q.get(i, 0)
    flat = g._shown_pixmap(1)
    assert flat.width() > flat.height()
    q[1] = 1
    g.rotation_changed(1)
    turned = g._shown_pixmap(1)
    assert turned.height() > turned.width() and turned.height() <= 130
    g.view.setCurrentIndex(g.model.index(0, 0))
    g.show_zoom()
    g.toggle_zoom()  # 100%
    assert pump(lambda: g._zoom_native)
    assert None in calls  # the native-size decode was asked for
    assert g._zoom_item.pixmap().width() == 600
    assert g._zoom_item.sceneBoundingRect().size().toTuple() == pytest.approx((400, 600))
    assert "100%" in g.zoom_caption.text() and "…" not in g.zoom_caption.text().split("·")[-1]
    q[1] = 0
    g.rotation_changed(1)
    assert g._zoom_item.sceneBoundingRect().size().toTuple() == pytest.approx((600, 400))
    assert not g._zoom_fit  # zoom state kept
    g.wait_idle()
    g.close()


def test_compare_cell_follows_rotation_and_keeps_zoom():
    features.set_mode(features.ADVANCED)
    try:
        cat = Cat3()
        cat = Catalog(cat.pictures)

        def loader(path, max_side):
            w, h = (800, 600) if max_side is None else (80, 60)
            return Image.new("RGB", (w, h)), SimpleNamespace(native=True)

        class Pf:
            def cached(self, i):
                return solid(80, 60)

            def info(self, i):
                return SimpleNamespace(width=800)

        view = CompareView(cat, Pf(), loader=loader)
        q = {0: 0}
        view.rotation_of = lambda i: q.get(i, 0)
        view.resize(900, 400)
        view.show()
        view.open([0, 1], 0)
        cell = view.cell(0)
        assert cell.shown_size() == (80, 60)
        q[0] = 1
        view.rotation_changed(0)
        assert cell.shown_size() == (60, 80) and view.cell(1).shown_size() == (80, 60)
        view.toggle_zoom()
        assert pump(lambda: cell.caption.text() == "100%")  # the loader said the pixels are native
        assert cell.shown_size() == (600, 800)
        c0 = cell.centre_fraction()
        q[0] = 3
        view.rotation_changed(0)
        assert view.is_zoomed() and cell.zoomed and cell.shown_size() == (600, 800)
        c1 = cell.centre_fraction()
        assert c1[0] == pytest.approx(c0[0], abs=0.02) and c1[1] == pytest.approx(c0[1], abs=0.02)
        view.shutdown()
        view.close()
    finally:
        features.set_mode(features.CLASSIC)


# ---- the real files (skip when absent) ----------------------------------------------------------------

@pytest.mark.skipif(not (REAL_SET / DNG_NAMES[0]).exists(), reason="needs the jes set")
def test_real_dng_z_shows_native_6000_not_the_capped_decode():
    path = REAL_SET / DNG_NAMES[0]
    capped, info = pv.load(path)
    assert max(capped.size) <= 5120 and (info.width, info.height) == (6000, 4000)
    v = viewer()
    v.show_image(0, pic_of(path), to_qimage(capped), info)
    v.toggle_zoom()
    assert pump(lambda: v.image_size() == (6000, 4000), timeout=30)
    assert v.zoom_text() == "100%"
    v.shutdown()
    v.close()
