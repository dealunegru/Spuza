"""Story fit (9:16 frame): the pure model and render (spuza.story) and the mouse editor (ui.story_overlay).

All data is synthetic: coloured quadrants so a render can be asserted by pixel colour.
"""

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from spuza import story as st

RED, GREEN, BLUE, YELLOW, BLACK = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 0, 0)
EXPORT = (1080, 1920)


def quad(w, h):
    """Quadrants: TL red, TR green, BL blue, BR yellow."""
    im = Image.new("RGB", (w, h))
    for box, c in (
        ((0, 0, w // 2, h // 2), RED),
        ((w // 2, 0, w, h // 2), GREEN),
        ((0, h // 2, w // 2, h), BLUE),
        ((w // 2, h // 2, w, h), YELLOW),
    ):
        im.paste(c, box)
    return im


def near(a, b, tol=40):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_scales():
    assert st.fit_scale(300, 200, 540, 960) == pytest.approx(1.8)
    assert st.cover_scale(300, 200, 540, 960) == pytest.approx(4.8)
    assert st.fit_scale(200, 300, 540, 960) == pytest.approx(2.7)
    assert st.cover_scale(200, 300, 540, 960) == pytest.approx(3.2)
    assert st.fit_scale(540, 960, 540, 960) == st.cover_scale(540, 960, 540, 960) == pytest.approx(1.0)


def test_fill_zoom():
    assert st.fill_story(300, 200).zoom == pytest.approx(4.8 / 1.8)
    assert st.fill_story(540, 960).zoom == pytest.approx(1.0)


def test_clamp_min_zoom_and_centering_when_not_covering():
    s = st.clamp(st.Story(0.2, 0.4, -0.4), 300, 200)
    assert s == st.Story(1.0, 0.0, 0.0)  # fit: neither axis overhangs


def test_clamp_per_axis_cover():
    fill = st.fill_story(300, 200)
    big = st.clamp(st.Story(fill.zoom, 5.0, 5.0), 300, 200)
    s = fill.zoom * st.fit_scale(300, 200, 1080, 1920)
    assert big.dy == pytest.approx(0.0, abs=1e-9)
    assert big.dx == pytest.approx((300 * s - 1080) / 2 / 1080)
    assert big.dx > 0


def test_clamp_mixed_axis_not_covering():
    s = st.clamp(st.Story(1.2, 0.5, 0.5), 300, 200)
    assert s.dx > 0  # width now overhangs the frame
    assert s.dy == 0.0  # height still does not cover


def test_max_scale_big_picture_is_native_when_below_8x_cover():
    assert st.max_scale(6000, 4000) == pytest.approx(1.0)
    assert st.max_scale(2000, 3000) == pytest.approx(1.0)


def test_max_scale_is_8x_cover_when_that_is_below_native():
    # 600x400: cover = 4.8 > native 1 -> max is the cover scale (native below cover)
    assert st.max_scale(600, 400) == pytest.approx(st.cover_scale(600, 400, *EXPORT))
    # 2200x3900 portrait-ish: cover = max(0.49, 0.4923)=0.492; 8*cover=3.9 > native 1 -> native
    # a case where 8*cover < native needs cover < 0.125: >8640 px tall
    assert st.max_scale(8000, 12000) == pytest.approx(1.0)
    assert st.max_scale(20000, 30000) == pytest.approx(8 * st.cover_scale(20000, 30000, *EXPORT))


def test_max_scale_tiny_picture_is_cover():
    assert st.max_scale(100, 50) == pytest.approx(st.cover_scale(100, 50, *EXPORT))
    s = st.clamp(st.Story(1000.0, 0, 0), 100, 50)
    assert s.zoom == pytest.approx(st.fill_story(100, 50).zoom)


def test_max_zoom_clamps_big_picture():
    s = st.clamp(st.Story(1000.0, 0, 0), 6000, 4000)
    assert s.zoom * st.fit_scale(6000, 4000, *EXPORT) == pytest.approx(1.0)


def test_zoom_about_keeps_point_fixed():
    W, H = 3000, 5000  # covers both axes at this zoom, so neither offset is re-centred
    story = st.Story(1.5, 0.05, 0.02)
    fw, fh = 540, 960
    p = (40.0, -70.0)
    sa = story.zoom * st.fit_scale(W, H, fw, fh)
    src = ((p[0] - story.dx * fw) / sa, (p[1] - story.dy * fh) / sa)
    new = st.zoom_about(story, W, H, p, factor=1.3, frame_size=(fw, fh))
    sb = new.zoom * st.fit_scale(W, H, fw, fh)
    assert new.zoom == pytest.approx(1.5 * 1.3)
    src2 = ((p[0] - new.dx * fw) / sb, (p[1] - new.dy * fh) / sb)
    assert src2[0] == pytest.approx(src[0], rel=1e-6)
    assert src2[1] == pytest.approx(src[1], rel=1e-6)


def test_zoom_about_explicit_zoom_and_clamp():
    r = st.zoom_about(st.Story(1, 0, 0), 300, 200, (0, 0), zoom=0.5)
    assert r.zoom == 1.0
    r = st.zoom_about(st.Story(1, 0, 0), 300, 200, (0, 0), zoom=99)
    assert r.zoom == pytest.approx(st.fill_story(300, 200).zoom)


def test_pan_clamped_and_resolution_independent():
    fill = st.fill_story(300, 200)
    a = st.pan(fill, 300, 200, 20, 999, (540, 960))
    b = st.pan(fill, 300, 200, 40, 999, (1080, 1920))
    assert a.dx == pytest.approx(b.dx)
    assert a.dy == 0.0
    assert a.dx == pytest.approx(20 / 540)


def test_steps_multiples_of_quarter():
    W, H = 6000, 4000
    fit = st.fit_scale(W, H, *EXPORT)
    s = st.Story(1.0, 0, 0)
    up = st.zoom_step(s, W, H, +1)
    assert up.zoom * fit == pytest.approx(0.25)
    up2 = st.zoom_step(up, W, H, +1)
    assert up2.zoom * fit == pytest.approx(0.5)
    dn = st.zoom_step(up2, W, H, -1)
    assert dn.zoom * fit == pytest.approx(0.25)
    dn2 = st.zoom_step(dn, W, H, -1)
    assert dn2.zoom == pytest.approx(1.0)
    top = st.clamp(st.Story(1000, 0, 0), W, H)
    assert st.zoom_step(top, W, H, +1).zoom == pytest.approx(top.zoom)


def test_visible_rect():
    r = st.visible_rect(st.Story(1, 0, 0), 300, 200)
    assert r == pytest.approx((0, 0, 300, 200))
    f = st.visible_rect(st.fill_story(300, 200), 300, 200)
    assert f[1] == pytest.approx(0)
    assert f[3] == pytest.approx(200)
    assert f[2] == pytest.approx(200 * 9 / 16)
    assert f[0] == pytest.approx((300 - f[2]) / 2)


def test_render_size_always_export():
    for w, h in ((300, 200), (200, 300), (90, 160), (100, 50)):
        for story in (st.Story(1, 0, 0), st.fill_story(w, h), st.Story(50, 3, -3)):
            out = st.render(quad(w, h), 0, story)
            assert out.size == EXPORT and out.mode == "RGB"


def test_render_fit_has_black_bands_for_3_2():
    out = st.render(quad(300, 200), 0, st.Story(1, 0, 0))
    assert out.getpixel((540, 20)) == BLACK
    assert out.getpixel((540, 1900)) == BLACK
    assert near(out.getpixel((200, 800)), RED)
    assert near(out.getpixel((880, 800)), GREEN)
    assert near(out.getpixel((200, 1120)), BLUE)
    assert near(out.getpixel((880, 1120)), YELLOW)


def test_render_fill_has_no_bands():
    out = st.render(quad(300, 200), 0, st.fill_story(300, 200))
    for xy in ((2, 2), (1077, 2), (2, 1917), (1077, 1917), (540, 5), (540, 1915)):
        assert out.getpixel(xy) != BLACK
    assert near(out.getpixel((2, 2)), RED)
    assert near(out.getpixel((1077, 1917)), YELLOW)


def test_render_turns_before_fit():
    # a landscape turned once clockwise is a portrait: the top-left red quadrant lands top-right
    out = st.render(quad(300, 200), 1, st.Story(1, 0, 0))
    assert out.getpixel((540, 10)) == BLACK  # 200x300 fits by width, bands above and below
    assert near(out.getpixel((880, 760)), RED)
    assert near(out.getpixel((200, 760)), BLUE)
    assert near(out.getpixel((880, 1160)), GREEN)
    assert near(out.getpixel((200, 1160)), YELLOW)
    a = st.render(quad(300, 200), 4, st.Story(1, 0, 0))
    b = st.render(quad(300, 200), 0, st.Story(1, 0, 0))
    assert a.getpixel((200, 800)) == b.getpixel((200, 800))


def test_render_pan_moves_photo():
    f = st.fill_story(300, 200)
    a = st.render(quad(300, 200), 0, st.Story(f.zoom, 5.0, 0))  # clamped to the pan limit
    b = st.render(quad(300, 200), 0, st.Story(f.zoom, -5.0, 0))
    assert near(a.getpixel((10, 100)), RED)
    assert near(b.getpixel((1070, 100)), GREEN)
    assert near(a.getpixel((1070, 100)), RED) or near(a.getpixel((1070, 100)), GREEN)


def test_story_identical_across_frame_sizes():
    story = st.Story(1.7, 0.03, -0.01)
    r0 = st.visible_rect(story, 3000, 2000)
    for fs in ((540, 960), (1080, 1920), (270, 480)):
        assert st.visible_rect(story, 3000, 2000, fs) == pytest.approx(r0)


def test_clamp_nan_and_garbage():
    s = st.clamp(st.Story(float("nan"), float("inf"), float("nan")), 300, 200)
    assert s == st.Story(1.0, 0.0, 0.0)
    assert st.clamp(st.Story(1, 0, 0), 0, 0) == st.Story(1.0, 0.0, 0.0)


def test_save_jpeg_strips_orientation(tmp_path):
    im = st.render(quad(300, 200), 0, st.Story(1, 0, 0))
    exif = Image.new("RGB", (4, 4)).getexif()
    exif[0x0112] = 6
    exif[0x010F] = "TestMaker"
    dst = tmp_path / "a.jpg"
    st.save(im, dst, exif)
    back = Image.open(dst)
    assert back.size == EXPORT and back.format == "JPEG" and back.mode == "RGB"
    e = back.getexif()
    assert e.get(0x0112, 1) == 1
    assert e.get(0x010F) == "TestMaker"
    st.save(im, tmp_path / "b.jpg")
    assert Image.open(tmp_path / "b.jpg").size == EXPORT


# ---- editor widget -------------------------------------------------------------------------------------------


@pytest.fixture
def qapp():
    return QApplication.instance()


def pm(w, h):
    p = QPixmap(w, h)
    p.fill(Qt.red)
    return p


@pytest.fixture
def editor(qapp):
    from spuza.ui.story_overlay import StoryEditor

    host = QWidget()
    host.resize(600, 800)
    ed = StoryEditor(host)
    ed.resize(600, 800)
    host.show()
    yield ed
    ed.end()
    host.close()


def wheel(ed, pos, delta):
    p = QPointF(*pos)
    ev = QWheelEvent(
        p, ed.mapToGlobal(p), QPoint(0, 0), QPoint(0, delta), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False
    )
    ed.wheelEvent(ev)


def test_editor_begin_default_is_fit(editor):
    assert not editor.active
    editor.begin(pm(300, 200), 0, None)
    assert editor.active
    assert editor.story() == st.Story(1.0, 0.0, 0.0)
    editor.end()
    assert not editor.active


def test_editor_begin_with_story_and_turns(editor):
    editor.begin(pm(3000, 2000), 1, st.Story(1.5, 0.0, 0.0))
    assert editor.story().zoom == pytest.approx(1.5)
    editor.begin(pm(300, 200), 0, st.Story(0.1, 9, 9))
    assert editor.story() == st.Story(1.0, 0.0, 0.0)


def test_editor_wheel_zooms_about_cursor_and_clamps(editor):
    editor.begin(pm(3000, 2000), 0, None)
    hits = []
    editor.changed.connect(lambda: hits.append(1))
    wheel(editor, (300, 400), 120)
    assert editor.story().zoom == pytest.approx(1.1)
    assert hits
    wheel(editor, (300, 400), -120)
    wheel(editor, (300, 400), -120)
    assert editor.story().zoom == 1.0
    for _ in range(200):
        wheel(editor, (300, 400), 120)
    assert editor.story().zoom == pytest.approx(st.clamp(st.Story(1e9, 0, 0), 3000, 2000).zoom)


def test_editor_drag_pans(editor):
    editor.begin(pm(300, 200), 0, st.fill_story(300, 200))
    x0 = editor.story().dx
    QTest.mousePress(editor, Qt.LeftButton, Qt.NoModifier, QPoint(300, 400))
    QTest.mouseMove(editor, QPoint(280, 380))
    QTest.mouseRelease(editor, Qt.LeftButton, Qt.NoModifier, QPoint(280, 380))
    assert editor.story().dx < x0
    assert editor.story().dy == 0.0


def test_editor_double_click_toggles_fit_fill(editor):
    editor.begin(pm(300, 200), 0, None)
    QTest.mouseDClick(editor, Qt.LeftButton, Qt.NoModifier, QPoint(300, 400))
    assert editor.story().zoom == pytest.approx(st.fill_story(300, 200).zoom)
    QTest.mouseDClick(editor, Qt.LeftButton, Qt.NoModifier, QPoint(300, 400))
    assert editor.story() == st.Story(1.0, 0.0, 0.0)


def test_editor_keys(editor):
    editor.begin(pm(3000, 2000), 0, None)
    applied, cancelled = [], []
    editor.applied.connect(lambda: applied.append(1))
    editor.cancelled.connect(lambda: cancelled.append(1))
    QTest.keyClick(editor, Qt.Key_Plus)
    z1 = editor.story().zoom
    assert z1 > 1.0
    QTest.keyClick(editor, Qt.Key_Equal)
    assert editor.story().zoom > z1
    QTest.keyClick(editor, Qt.Key_Minus)
    assert editor.story().zoom == pytest.approx(z1)
    QTest.keyClick(editor, Qt.Key_0)
    assert editor.story() == st.Story(1.0, 0.0, 0.0)
    QTest.keyClick(editor, Qt.Key_Return)
    QTest.keyClick(editor, Qt.Key_Enter)
    assert len(applied) == 2
    QTest.keyClick(editor, Qt.Key_Escape)
    assert cancelled == [1]


def test_editor_public_methods(editor):
    editor.begin(pm(3000, 2000), 0, None)
    editor.zoom_step(1)
    assert editor.story().zoom > 1
    editor.reset()
    assert editor.story().zoom == 1.0


def test_editor_paints_frame_and_darkens_outside(editor):
    editor.begin(pm(300, 200), 1, st.fill_story(200, 300))
    img = editor.grab().toImage()
    assert img.width() == 600
    centre = img.pixelColor(300, 400)
    assert centre.red() > 200 and centre.green() < 60  # the red photo inside the frame
    assert img.pixelColor(2, 2).red() < 200  # darkened outside


def test_editor_uses_source_size_for_limits_and_steps(editor):
    # a 1920x1280 preview of a 6000x4000 source: limits and the step grid are the source's
    editor.begin(pm(1920, 1280), 0, None, source_size=(6000, 4000))
    fit = st.fit_scale(6000, 4000)
    for _ in range(300):
        editor.zoom_step(1)
    assert editor.story().zoom * fit == pytest.approx(1.0)
    assert editor.story().zoom == pytest.approx(st.clamp(st.Story(1e9, 0, 0), 6000, 4000).zoom)
    editor.reset()
    editor.zoom_step(1)
    assert editor.story().zoom * fit == pytest.approx(0.25)
    editor.zoom_step(1)
    assert editor.story().zoom * fit == pytest.approx(0.5)


def test_editor_source_size_follows_turns(editor):
    editor.begin(pm(300, 200), 1, st.fill_story(4000, 6000), source_size=(6000, 4000))
    assert editor.story().zoom == pytest.approx(st.fill_story(4000, 6000).zoom)


def test_editor_paint_matches_render(editor):
    from PySide6.QtGui import QImage

    src = quad(300, 200)
    story = st.clamp(st.Story(2.6, 0.12, -0.05), 200, 300)  # turned size is 200x300
    qi = QImage(src.tobytes(), 300, 200, 900, QImage.Format.Format_RGB888)
    editor.begin(QPixmap.fromImage(qi), 1, story, source_size=(300, 200))
    assert editor.story() == story
    grab = editor.grab().toImage()
    fr = editor._frame()
    x0, y0, fw, fh = round(fr.x()), round(fr.y()), round(fr.width()), round(fr.height())
    ref = st.render(src, 1, story).resize((fw, fh), Image.Resampling.BILINEAR)
    bad = total = 0
    for yy in range(4, fh - 4, 9):
        for xx in range(4, fw - 4, 9):
            c = grab.pixelColor(x0 + xx, y0 + yy)
            r = ref.getpixel((xx, yy))
            total += 1
            bad += max(abs(c.red() - r[0]), abs(c.green() - r[1]), abs(c.blue() - r[2])) > 80
    assert bad / total < 0.04
