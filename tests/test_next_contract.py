"""The contract the four next-feature clusters build on (spuza-features-r1): flags, settings keys, the
PreviewInfo field, the viewer hooks, and the module surfaces published as stubs."""

from __future__ import annotations

import importlib
import inspect

from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from spuza import features
from spuza.preview import PreviewInfo
from spuza.ui import settings
from spuza.ui.viewer import Viewer

NEW_FLAGS = ("compare_view", "focus_peaking", "focus_point", "resume_session")


def test_new_flags_are_advanced_only():
    for name in NEW_FLAGS:
        assert features.FLAGS[name].classic is False
        assert not features.enabled(name)
    features.set_mode(features.ADVANCED)
    assert all(features.enabled(n) for n in NEW_FLAGS)
    assert features.classic_flags() == frozenset()


def test_settings_keys_defaults_and_round_trip():
    assert (settings.PEAKING_ON, settings.PEAKING_THRESHOLD, settings.FOCUS_MARKER, settings.RESUME_ON) == (
        "peaking/on", "peaking/threshold", "focus/marker", "resume/enabled")
    s = settings.open_settings()
    assert settings.read_peaking_on(s) is False
    assert settings.read_peaking_threshold(s) == settings.DEFAULT_PEAKING_THRESHOLD == 60
    assert settings.read_focus_marker(s) is False
    assert settings.read_resume_on(s) is True
    settings.write_peaking_on(s, True)
    settings.write_peaking_threshold(s, 999)
    settings.write_focus_marker(s, True)
    settings.write_resume_on(s, False)
    s.sync()
    s2 = settings.open_settings()
    assert settings.read_peaking_on(s2) and settings.read_focus_marker(s2)
    assert settings.read_peaking_threshold(s2) == 255
    assert settings.read_resume_on(s2) is False


def test_preview_info_focus_defaults_to_none():
    info = PreviewInfo("x", "pillow", 1, 1, 1, 1.0)
    assert info.focus is None


def _viewer_with(w=400, h=300):
    v = Viewer()
    v.resize(200, 150)
    v.show()
    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(QColor(100, 100, 100))
    return v, img


def test_zoom_target_none_centres_like_before():
    v, img = _viewer_with()
    seen = []
    v.pixmap_changed.connect(seen.append)
    v.index = 0  # show_image needs a Picture; _put is the path it takes
    v._put(img, keep_centre=False)
    assert seen == [0]
    v.toggle_zoom()
    QTest.qWait(1)
    c = v.mapToScene(v.viewport().rect().center())
    assert abs(c.x() - 200) <= 2 and abs(c.y() - 150) <= 2
    v.shutdown()


def test_zoom_target_centres_on_the_fraction():
    v, img = _viewer_with()
    v.zoom_target = lambda: (0.75, 0.25)
    v.index = 0
    v._put(img, keep_centre=False)
    v.toggle_zoom()
    QTest.qWait(1)
    c = v.mapToScene(v.viewport().rect().center())
    assert abs(c.x() - 300) <= 2 and abs(c.y() - 75) <= 2
    assert v.image_item() is v._item
    v.shutdown()


def test_zoom_target_that_raises_falls_back_to_centre():
    v, img = _viewer_with()

    def boom():
        raise RuntimeError("bad")

    v.zoom_target = boom
    v.index = 0
    v._put(img, keep_centre=False)
    v.toggle_zoom()
    c = v.mapToScene(v.viewport().rect().center())
    assert abs(c.x() - 200) <= 2
    v.shutdown()


SURFACES = {
    "spuza.peaking": ["edge_mask", "mask_image"],
    "spuza.ui.peaking_overlay": ["PeakingOverlay"],
    "spuza.focus": ["ALLOWLIST", "FocusInfo", "read"],
    "spuza.ui.focus_marker": ["FocusMarker"],
    "spuza.ui.compare": ["CompareView", "MAX_CELLS"],
    "spuza.session": ["Restored", "SessionStore", "card_key", "default_dir", "PRUNE_DAYS"],
    "spuza.ui.resume": ["ResumeController"],
}


def test_module_surfaces_exist():
    for mod, names in SURFACES.items():
        m = importlib.import_module(mod)
        for n in names:
            assert hasattr(m, n), f"{mod}.{n}"
    from spuza.ui.compare import CompareView
    from spuza.ui.peaking_overlay import PeakingOverlay

    for cls, methods in ((CompareView, ["open", "indices", "focused_index", "is_zoomed", "set_advance_on_mark",
                                        "set_overlay", "status_text", "close_compare", "shutdown"]),
                         (PeakingOverlay, ["is_on", "set_on", "set_threshold", "show", "mask_for", "shutdown"])):
        for name in methods:
            assert inspect.isfunction(getattr(cls, name)), f"{cls.__name__}.{name}"
    from spuza.focus import ALLOWLIST

    assert ALLOWLIST == frozenset({"X-T4", "X-T5", "X100V", "X-T50"})
