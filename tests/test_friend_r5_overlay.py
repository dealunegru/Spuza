"""CropOverlay: drawing, moving, resizing, clamping, rotation, pixmap swap, mouse transparency."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from spuza.ui.crop_overlay import CropOverlay


def _pm(w, h) -> QPixmap:
    p = QPixmap(w, h)
    p.fill(Qt.GlobalColor.gray)
    return p


class Rig:
    def __init__(self, w=400, h=300):
        self.scene = QGraphicsScene()
        self.item = QGraphicsPixmapItem(_pm(w, h))
        self.scene.addItem(self.item)
        self.view = QGraphicsView(self.scene)
        self.view.resize(700, 600)
        self.view.show()
        self.ov = CropOverlay(self.item)

    def vp(self, ix, iy):
        """Viewport point of an item-coordinate point (any parent rotation)."""
        return self.view.mapFromScene(self.item.mapToScene(QPointF(ix, iy)))

    def _send(self, kind, pt: QPoint, buttons):
        btn = Qt.MouseButton.LeftButton
        ev = QMouseEvent(kind, QPointF(pt), QPointF(self.view.viewport().mapToGlobal(pt)), btn, buttons,
                         Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.view.viewport(), ev)

    def drag(self, a, b):
        """Left-drag from item point a to item point b."""
        pa, pb = self.vp(*a), self.vp(*b)
        self._send(QEvent.Type.MouseButtonPress, pa, Qt.MouseButton.LeftButton)
        self._send(QEvent.Type.MouseMove, pb, Qt.MouseButton.LeftButton)
        self._send(QEvent.Type.MouseButtonRelease, pb, Qt.MouseButton.NoButton)

    def close(self):
        self.view.close()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def _approx(got, want):
    assert got is not None
    assert got == pytest.approx(want, abs=0.01)


def test_idle_is_invisible_and_mouse_transparent(rig):
    ov = rig.ov
    assert not ov.editing and ov.rect_frac() is None
    assert not ov.isVisible() or ov.acceptedMouseButtons() == Qt.MouseButton.NoButton
    rig.drag((50, 50), (200, 200))
    assert ov.rect_frac() is None


def test_draw_new_rectangle(rig):
    rig.ov.begin()
    assert rig.ov.editing and rig.ov.isVisible()
    rig.drag((100, 60), (300, 240))
    _approx(rig.ov.rect_frac(), (0.25, 0.2, 0.75, 0.8))


def test_draw_backwards_is_ordered(rig):
    rig.ov.begin()
    rig.drag((300, 240), (100, 60))
    _approx(rig.ov.rect_frac(), (0.25, 0.2, 0.75, 0.8))


def test_begin_with_rect_shows_it(rig):
    rig.ov.begin((0.1, 0.1, 0.5, 0.6))
    _approx(rig.ov.rect_frac(), (0.1, 0.1, 0.5, 0.6))


def test_move_inside_and_clamp(rig):
    rig.ov.begin((0.25, 0.25, 0.5, 0.5))  # item px 100..200 x 75..150
    rig.drag((150, 110), (190, 130))  # +40, +20
    _approx(rig.ov.rect_frac(), (0.35, 0.3166, 0.6, 0.5666))
    rig.drag((190, 130), (4000, 4000))  # grab inside the moved rect, fling far away
    r = rig.ov.rect_frac()
    assert r[2] == pytest.approx(1.0) and r[3] == pytest.approx(1.0)
    assert r[2] - r[0] == pytest.approx(0.25, abs=0.01)


def test_resize_from_corner(rig):
    rig.ov.begin((0.25, 0.25, 0.75, 0.75))  # 100..300 x 75..225
    rig.drag((300, 225), (360, 255))
    _approx(rig.ov.rect_frac(), (0.25, 0.25, 0.9, 0.85))


def test_resize_from_edge_midpoint(rig):
    rig.ov.begin((0.25, 0.25, 0.75, 0.75))
    rig.drag((100, 150), (40, 150))  # left edge midpoint
    _approx(rig.ov.rect_frac(), (0.1, 0.25, 0.75, 0.75))


def test_resize_clamps_at_picture_edge(rig):
    rig.ov.begin((0.25, 0.25, 0.75, 0.75))
    rig.drag((300, 150), (3000, 150))
    _approx(rig.ov.rect_frac(), (0.25, 0.25, 1.0, 0.75))


def test_tiny_rect_is_none(rig):
    rig.ov.begin()
    rig.drag((100, 100), (102, 102))
    assert rig.ov.rect_frac() is None
    rig.ov.set_rect_frac((0.2, 0.2, 0.205, 0.9))
    assert rig.ov.rect_frac() is None


def test_set_rect_frac_clamps_and_orders(rig):
    rig.ov.set_rect_frac((1.5, -0.2, 0.5, 0.7))
    _approx(rig.ov.rect_frac(), (0.5, 0.0, 1.0, 0.7))
    rig.ov.set_rect_frac(None)
    assert rig.ov.rect_frac() is None


def test_garbage_rect_is_ignored(rig):
    for bad in ((float("nan"), 0, 1, 1), (0, 0, 1), "abc", 5):
        rig.ov.set_rect_frac(bad)
        assert rig.ov.rect_frac() is None


def test_end_makes_it_mouse_transparent(rig):
    rig.ov.begin((0.25, 0.25, 0.75, 0.75))
    rig.ov.end()
    assert not rig.ov.editing
    assert rig.ov.acceptedMouseButtons() == Qt.MouseButton.NoButton
    before = rig.ov.rect_frac()
    rig.drag((150, 150), (250, 250))
    assert rig.ov.rect_frac() == before


def test_rotation_maps_drag_to_fractions(rig):
    rig.item.setTransformOriginPoint(200, 150)
    rig.item.setRotation(90)
    rig.ov.begin()
    rig.drag((100, 60), (300, 240))  # item-space points, driven through the rotated view
    _approx(rig.ov.rect_frac(), (0.25, 0.2, 0.75, 0.8))


def test_pixmap_swap_keeps_fractions(rig):
    rig.ov.begin((0.25, 0.25, 0.75, 0.75))
    rig.item.setPixmap(_pm(800, 600))
    rig.ov.sync()
    _approx(rig.ov.rect_frac(), (0.25, 0.25, 0.75, 0.75))
    assert rig.ov.boundingRect().contains(QPointF(790, 590))
    rig.drag((700, 500), (760, 560))  # empty area in the new 800x600 frame: draws a new rectangle
    _approx(rig.ov.rect_frac(), (0.875, 0.8333, 0.95, 0.9333))


def test_changed_emitted_while_editing(rig):
    hits = []
    rig.ov.changed.connect(lambda: hits.append(1))
    rig.ov.begin()
    rig.drag((100, 60), (300, 240))
    assert hits


def test_show_applied_is_passive(rig):
    rig.ov.show_applied((0.1, 0.1, 0.6, 0.6))
    assert rig.ov.isVisible() and not rig.ov.editing
    assert rig.ov.acceptedMouseButtons() == Qt.MouseButton.NoButton
    rig.drag((50, 50), (300, 200))
    assert rig.ov.rect_frac() is None  # the applied crop is not an editing rectangle
    rig.ov.show_applied(None)
    assert not rig.ov.isVisible()


def test_paints_without_error(rig):
    rig.ov.begin((0.2, 0.2, 0.8, 0.8))
    assert not rig.view.grab().isNull()
    rig.ov.end()
    rig.ov.show_applied((0.2, 0.2, 0.8, 0.8))
    assert not rig.view.grab().isNull()


# -- round 6: the ratio lock ---------------------------------------------------------------------------------
NATIVE = (6000, 4000)  # the 400 x 300 stand-in pixmap is 4:3, the "picture" 3:2: the fraction aspect must use NATIVE


def _locked(rig, aspect=0.8, label="4:5"):
    rig.ov.begin()
    rig.ov.set_aspect(aspect, NATIVE, label)


def _px_aspect(rect):
    return (rect[2] - rect[0]) * NATIVE[0] / ((rect[3] - rect[1]) * NATIVE[1])


def test_locked_has_only_four_corner_handles_and_free_keeps_eight(rig):
    rig.ov.begin((0.25, 0.25, 0.75, 0.75))
    rect = rig.ov._px(rig.ov._rect)
    assert len(rig.ov._handles(rect)) == 8
    rig.ov.set_aspect(0.8, NATIVE, "4:5")
    assert sorted(rig.ov._handles(rect)) == [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    rig.ov.set_aspect(None, NATIVE)
    assert len(rig.ov._handles(rect)) == 8 and rig.ov.aspect is None


def test_locked_corner_drag_keeps_the_ratio_and_the_opposite_corner(rig):
    _locked(rig)
    h = 0.3 * NATIVE[0] / 0.8 / NATIVE[1]
    start = (0.2, 0.2, 0.5, 0.2 + h)
    rig.ov.set_rect_frac(start)
    assert _px_aspect(rig.ov.rect_frac()) == pytest.approx(0.8, rel=1e-6)
    right, bottom = 0.5 * 400, (0.2 + h) * 300
    rig.drag((right, bottom), (right + 60, bottom + 5))  # the bottom-right corner, pulled out
    r = rig.ov.rect_frac()
    assert (r[0], r[1]) == pytest.approx((0.2, 0.2))  # the opposite corner is fixed
    assert _px_aspect(r) == pytest.approx(0.8, rel=1e-3)
    assert r[2] > 0.5 and r[3] > 0.2 + h
    left, top = 0.2 * 400, 0.2 * 300
    rig.drag((left, top), (left - 30, top - 20))  # the top-left corner of the new rectangle is not at the edge
    assert _px_aspect(rig.ov.rect_frac()) == pytest.approx(0.8, rel=1e-3)


def test_locked_corner_drag_is_clamped_inside_the_picture(rig):
    _locked(rig)
    h = 0.3 * NATIVE[0] / 0.8 / NATIVE[1]
    rig.ov.set_rect_frac((0.2, 0.2, 0.5, 0.2 + h))
    rig.drag((0.5 * 400, (0.2 + h) * 300), (5000, 5000))
    r = rig.ov.rect_frac()
    assert 0.0 <= r[0] and r[2] <= 1.0 + 1e-9 and r[3] <= 1.0 + 1e-9
    assert _px_aspect(r) == pytest.approx(0.8, rel=1e-3) and (r[0], r[1]) == pytest.approx((0.2, 0.2))
    assert r[3] == pytest.approx(1.0, abs=1e-6) or r[2] == pytest.approx(1.0, abs=1e-6)


def test_locked_drag_on_empty_space_draws_a_new_rectangle_at_the_ratio(rig):
    _locked(rig, 1.0, "1:1")
    rig.drag((100, 60), (300, 100))
    r = rig.ov.rect_frac()
    assert _px_aspect(r) == pytest.approx(1.0, rel=1e-3) and (r[0], r[1]) == pytest.approx((0.25, 0.2))
    rig.drag((300, 240), (100, 60))  # backwards works too
    assert _px_aspect(rig.ov.rect_frac()) == pytest.approx(1.0, rel=1e-3)


def test_locked_inside_drag_moves(rig):
    _locked(rig)
    h = 0.3 * NATIVE[0] / 0.8 / NATIVE[1]
    rig.ov.set_rect_frac((0.2, 0.2, 0.5, 0.2 + h))
    before = rig.ov.rect_frac()
    rig.drag((0.35 * 400, (0.2 + h / 2) * 300), (0.45 * 400, (0.2 + h / 2) * 300))
    after = rig.ov.rect_frac()
    assert after[2] - after[0] == pytest.approx(before[2] - before[0]) and after[0] == pytest.approx(0.3, abs=0.01)


def test_free_drag_still_unconstrained_after_unlocking(rig):
    _locked(rig)
    rig.ov.set_aspect(None, NATIVE)
    rig.ov.begin()
    rig.drag((100, 60), (300, 240))
    _approx(rig.ov.rect_frac(), (0.25, 0.2, 0.75, 0.8))


def test_set_aspect_rejects_junk_and_falls_back_to_free(rig):
    for bad in (None, 0, -1, float("nan"), float("inf"), "x", True):
        rig.ov.set_aspect(bad, NATIVE, "4:5")
        assert rig.ov.aspect is None
    for bad in (None, (0, 0), (1,), "ab", (float("nan"), 1)):
        rig.ov.set_aspect(0.8, bad, "4:5")
        assert rig.ov.aspect is None


def test_label_text_has_the_ratio_and_the_pixels(rig):
    rig.ov.begin()
    rig.ov.set_aspect(0.8, NATIVE, "4:5")
    rig.ov.set_rect_frac(ratio_largest(0.8))
    assert rig.ov.label_text() == "4:5 · 3200 × 4000 px"
    rig.ov.set_aspect(None, NATIVE, "Free")
    rig.ov.set_rect_frac((0.0, 0.0, 0.62, 0.62))
    assert rig.ov.label_text() == "Free · 3720 × 2480 px"


def ratio_largest(a):
    from spuza import ratio

    return ratio.largest_centred(a, *NATIVE)


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_label_is_upright_on_screen_whatever_the_turn(rig, turn):
    from PySide6.QtGui import QColor

    rig.item.setTransformOriginPoint(200, 150)
    rig.item.setRotation(turn)
    rig.ov.begin()
    rig.ov.set_aspect(None, NATIVE, "Free")
    rig.ov.set_rect_frac((0.1, 0.1, 0.9, 0.9))
    img = rig.view.viewport().grab().toImage()
    c = rig.view.mapFromScene(rig.item.mapToScene(QPointF(200, 150)))
    xs, ys = [], []
    for y in range(max(0, c.y() - 60), min(img.height(), c.y() + 60)):
        for x in range(max(0, c.x() - 150), min(img.width(), c.x() + 150)):
            col = QColor(img.pixel(x, y))
            if col.red() < 75 and col.green() < 75 and col.blue() < 75 and abs(x - c.x()) < 140:
                xs.append(x)
                ys.append(y)
    assert xs, "no label pill found"
    assert max(xs) - min(xs) > 2 * (max(ys) - min(ys))  # wider than tall: the text is not turned with the picture
