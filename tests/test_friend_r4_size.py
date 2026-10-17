"""The inspector shows the PICTURE's size; the embedded preview's size sits on its own line."""

import threading
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QEventLoop, QTimer

from spuza import picsize, preview
from spuza.catalog import Catalog, Picture, Prefetcher
from spuza.preview import PreviewInfo
from spuza.ui.inspector import Inspector


def _jpeg(path: Path, size=(40, 20), orient=None) -> Path:
    im = Image.new("RGB", size, (10, 20, 30))
    if orient:
        ex = Image.Exif()
        ex[0x0112] = orient
        im.save(path, "JPEG", exif=ex)
    else:
        im.save(path, "JPEG")
    return path


def _pair(tmp_path, side: Path | None) -> Picture:
    raw = tmp_path / "A1.RAF"
    raw.write_bytes(b"x")
    files = (raw,) + ((side,) if side else ())
    return Picture("A1", raw, side, files, "RAF+JPG" if side else "RAF", 10, 0.0)


def _info(path, w=1920, h=1280):
    return PreviewInfo(Path(path), "raf-embedded", w, h, 10, 2.0)


def test_sidecar_size_from_header(tmp_path):
    pic = _pair(tmp_path, _jpeg(tmp_path / "A1.JPG"))
    assert picsize.picture_size(pic, _info(pic.primary)) == ((40, 20), "jpeg-sidecar")


@pytest.mark.parametrize("orient,want", [(1, (40, 20)), (6, (20, 40)), (8, (20, 40)), (3, (40, 20))])
def test_sidecar_orientation_swap(tmp_path, orient, want):
    pic = _pair(tmp_path, _jpeg(tmp_path / "A1.JPG", orient=orient))
    assert picsize.picture_size(pic, _info(pic.primary))[0] == want


def test_raw_without_jpeg_uses_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "frame_size", lambda p: (5998, 3997))
    pic = _pair(tmp_path, None)
    assert picsize.picture_size(pic, _info(pic.primary)) == ((5998, 3997), "raw-frame")


def test_hif_sidecar_is_not_a_jpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "frame_size", lambda p: (6000, 4000))
    hif = tmp_path / "A1.HIF"
    hif.write_bytes(b"x")
    pic = _pair(tmp_path, hif)
    assert picsize.picture_size(pic, _info(pic.primary)) == ((6000, 4000), "raw-frame")


def test_unreadable_sidecar_falls_back_to_raw(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "frame_size", lambda p: (6000, 4000))
    bad = tmp_path / "A1.JPG"
    bad.write_bytes(b"not a jpeg")
    pic = _pair(tmp_path, bad)
    assert picsize.picture_size(pic, _info(pic.primary)) == ((6000, 4000), "raw-frame, sidecar unreadable")
    monkeypatch.setattr(preview, "frame_size", lambda p: None)  # and the frame fails too
    assert picsize.picture_size(pic, _info(pic.primary)) == ((1920, 1280), "decoded, sidecar unreadable")


def test_frame_size_raising_degrades_to_decoded(tmp_path, monkeypatch):
    def boom(p):
        raise RuntimeError("rawpy exploded")

    monkeypatch.setattr(preview, "frame_size", boom)
    pic = _pair(tmp_path, None)
    assert picsize.picture_size(pic, _info(pic.primary)) == ((1920, 1280), "decoded")


def test_plain_jpeg_is_decoded_size(tmp_path):
    jp = _jpeg(tmp_path / "P.JPG")
    pic = Picture("P", jp, None, (jp,), "JPG", 1, 0.0)
    info = PreviewInfo(jp, "pillow", 40, 20, 1, 1.0)
    assert picsize.picture_size(pic, info) == ((40, 20), "decoded")


def test_with_picture_size_leaves_foreign_objects_alone(tmp_path):
    pic = _pair(tmp_path, None)
    for obj in (object(), "text", None, {"width": 1}):
        assert picsize.with_picture_size(pic, obj) is obj


def _run(pf, stop_signal):
    loop = QEventLoop()
    stop_signal.connect(lambda *a: loop.quit())
    QTimer.singleShot(10000, loop.quit)  # bounded
    pf.update(0)
    loop.exec()


def test_ready_is_not_delayed_by_the_raw_frame(tmp_path, monkeypatch):
    """The image is emitted with the size still open; the frame arrives later on `sized`, from the worker."""
    main = threading.get_ident()
    seen = []

    def fake_frame(p):
        seen.append(threading.get_ident())
        return (6000, 4000)

    monkeypatch.setattr(preview, "frame_size", fake_frame)
    raw = tmp_path / "A1.RAF"
    raw.write_bytes(b"x")
    cat = Catalog([Picture("A1", raw, None, (raw,), "RAF", 1, 0.0)])
    pf = Prefetcher(cat, loader=lambda p, m: (Image.new("RGB", (8, 6)), _info(p)), threads=1)
    at_ready = []
    pf.ready.connect(lambda i, img, info: at_ready.append(info.picture_source))
    _run(pf, pf.sized)
    info = pf.info(0)
    pf.shutdown()
    assert at_ready == [None]  # the ready info carries no frame size yet
    assert info.picture_size == (6000, 4000) and info.picture_source == "raw-frame"
    assert seen and main not in seen


def test_sidecar_size_arrives_with_ready(tmp_path):
    pic = _pair(tmp_path, _jpeg(tmp_path / "A1.JPG", size=(60, 40)))
    pf = Prefetcher(Catalog([pic]), loader=lambda p, m: (Image.new("RGB", (8, 6)), _info(p)), threads=1)
    _run(pf, pf.ready)
    info = pf.info(0)
    pf.shutdown()
    assert info.picture_size == (60, 40) and info.picture_source == "jpeg-sidecar"


def test_inspector_marks_a_still_unknown_picture_size(tmp_path):
    pic = _pair(tmp_path, None)
    w = Inspector()
    w.set_picture(pic, _info(pic.primary, 1920, 1280))
    assert "picture size still reading" in w.plain()


def test_prefetcher_survives_foreign_info_and_failed_read(tmp_path):
    raw = tmp_path / "A1.RAF"
    raw.write_bytes(b"x")
    cat = Catalog([Picture("A1", raw, None, (raw,), "RAF", 1, 0.0)])
    pf = Prefetcher(cat, loader=lambda p, m: (Image.new("RGB", (4, 4)), "plain-object"), threads=1)
    _run(pf, pf.ready)
    got = pf.info(0)
    pf.shutdown()
    assert got == "plain-object"


def test_inspector_lines_pair(tmp_path):
    pic = _pair(tmp_path, _jpeg(tmp_path / "A1.JPG", size=(60, 40)))
    size, src = picsize.picture_size(pic, _info(pic.primary))
    info = PreviewInfo(pic.primary, "raf-embedded", 1920, 1280, 10, 2.0, picture_size=size, picture_source=src)
    w = Inspector()
    w.set_picture(pic, info)
    lines = w.plain().splitlines()
    assert "Size: 60 x 40" in lines
    assert "Preview: raf-embedded 1920 x 1280" in lines
    assert "Decode: 2.0 ms" in lines
    assert not any("unreadable" in ln for ln in lines)


def test_inspector_says_when_jpg_unreadable(tmp_path):
    pic = _pair(tmp_path, None)
    info = PreviewInfo(pic.primary, "raf-embedded", 1920, 1280, 10, 2.0, picture_size=(6000, 4000),
                       picture_source="raw-frame, sidecar unreadable")
    w = Inspector()
    w.set_picture(pic, info)
    assert "JPG beside it unreadable: zoom uses the raw" in w.plain()
    assert "Size: 6000 x 4000" in w.plain()


def test_inspector_without_picture_size_uses_decoded(tmp_path):
    pic = _pair(tmp_path, None)
    w = Inspector()
    w.set_picture(pic, _info(pic.primary, 100, 50))
    assert "Size: 100 x 50" in w.plain()
