"""Focus peaking edge mask: pure numpy, no widgets."""

import numpy as np
import pytest
from PySide6.QtGui import QImage

from spuza import peaking


def _checker(h=96, w=128, cell=8):
    yy, xx = np.mgrid[0:h, 0:w]
    g = (((yy // cell) + (xx // cell)) % 2 * 255).astype(np.uint8)
    return np.dstack([g, g, g])


def _blur(a, n=3):
    a = a.astype(np.float32)
    for _ in range(n):
        p = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
        a = sum(p[i : i + a.shape[0], j : j + a.shape[1]] for i in range(3) for j in range(3)) / 9
    return a.astype(np.uint8)


def _qimage(a):
    h, w, _ = a.shape
    return QImage(a.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888).copy()


def test_sharp_has_many_more_edges_than_blurred():
    sharp = (_checker(cell=4) // 2 + 60).astype(np.uint8)  # fine texture, levels 60 and 187
    soft = _blur(sharp, 6)
    assert peaking.edge_mask(sharp, 60, half=False).sum() > 5 * max(1, peaking.edge_mask(soft, 60, half=False).sum())


def test_flat_image_has_no_edges():
    assert not peaking.edge_mask(np.full((40, 50, 3), 128, np.uint8), 1).any()


def test_threshold_is_monotonic():
    a = _blur(_checker(), 1)
    counts = [int(peaking.edge_mask(a, t, half=False).sum()) for t in (1, 20, 60, 120, 255)]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > counts[-1]


@pytest.mark.parametrize("h,w", [(96, 128), (95, 127), (7, 9)])
def test_half_shape(h, w):
    a = np.zeros((h, w, 3), np.uint8)
    assert peaking.edge_mask(a, 10, half=True).shape == ((h + 1) // 2, (w + 1) // 2)
    assert peaking.edge_mask(a, 10, half=False).shape == (h, w)


def test_tiny_and_bad_input():
    assert peaking.edge_mask(np.zeros((2, 2, 3), np.uint8), 5).shape == (1, 1)
    assert not peaking.edge_mask(np.zeros((0, 0, 3), np.uint8), 5, half=False).any()
    with pytest.raises(ValueError):
        peaking.edge_mask(np.zeros((4, 4), np.uint8), 5)


def test_mask_image_is_argb_transparent_off_edge():
    img = _qimage(_checker())
    out = peaking.mask_image(img, 60, half=True, color=(10, 20, 30))
    assert out.format() == QImage.Format.Format_ARGB32
    assert (out.width(), out.height()) == (64, 48)
    assert out.pixelColor(0, 0).alpha() == 0  # border is never an edge
    edge = [(x, y) for y in range(48) for x in range(64) if out.pixelColor(x, y).alpha()]
    assert edge
    c = out.pixelColor(*edge[0])
    assert (c.red(), c.green(), c.blue()) == (10, 20, 30)
    assert peaking.mask_image(img, 60, half=False).width() == 128


def test_mask_image_handles_strided_qimage():
    a = _checker(30, 31)  # 93 bytes per row: QImage pads rows to 4
    out = peaking.mask_image(_qimage(a), 60, half=False)
    assert (out.width(), out.height()) == (31, 30)
