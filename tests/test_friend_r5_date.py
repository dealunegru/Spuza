"""Round 5: the inspector shows when a picture was taken, as one line.

EXIF DateTimeOriginal, else DateTimeDigitized, else the file's mtime (labelled). Camera clock as written.
Synthetic files only, made in tmp_path.
"""

import os
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from spuza import exif as ex
from spuza.catalog import Picture, scan_card
from spuza.ui.inspector import Inspector

from test_capture import jpeg, raf, tiff

ORIG = "2026:10:18 21:43:05"
DIGI = "2025:01:02 03:04:05"


def _jpg(path: Path, original=None, digitized=None) -> Path:
    e = Image.Exif()
    sub = e.get_ifd(0x8769)
    if original:
        sub[0x9003] = original
    if digitized:
        sub[0x9004] = digitized
    Image.new("RGB", (8, 6), "gray").save(path, "JPEG", exif=e)
    return path


def _scan_one(root: Path) -> Picture:
    pics = scan_card(root).pictures
    assert len(pics) == 1
    return pics[0]


def _taken_lines(w: Inspector) -> list[str]:
    return [t for t in w.plain().splitlines() if t.startswith("Taken:")]


def test_original_wins_and_format_is_exact(tmp_path):
    _jpg(tmp_path / "a.jpg", ORIG, DIGI)
    pic = _scan_one(tmp_path)
    w = Inspector()
    w.set_picture(pic, None)
    assert _taken_lines(w) == ["Taken: 2026-10-18  21:43:05"]
    assert "Taken: 2026-10-18  21:43:05" in w.label.text()


def test_digitized_fallback_through_preview_exif(tmp_path):
    p = _jpg(tmp_path / "a.jpg", None, DIGI)
    got = ex.read(path=p)
    assert got is not None and got.taken == "2025-01-02T03:04:05"
    pic = _scan_one(tmp_path)
    w = Inspector()
    from spuza.preview import PreviewInfo
    w.set_picture(pic, PreviewInfo(p, "pillow", 8, 6, 100, 1.0, got))
    assert _taken_lines(w) == ["Taken: 2025-01-02  03:04:05"]


def test_mtime_fallback_is_labelled_and_local(tmp_path):
    p = _jpg(tmp_path / "a.jpg")
    when = datetime(2024, 7, 9, 8, 7, 6)
    ts = time.mktime(when.timetuple())
    os.utime(p, (ts, ts))
    w = Inspector()
    w.set_picture(_scan_one(tmp_path), None)
    assert _taken_lines(w) == ["Taken: 2024-07-09  08:07:06 (file date)"]


def test_raw_shaped_files_use_the_scan_time(tmp_path):
    (tmp_path / "dng1.dng").write_bytes(tiff(ORIG.encode()))
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "raf1.raf").write_bytes(raf(jpeg(tiff(DIGI.replace("2025", "2026").encode()))))
    pics = {p.stem: p for p in scan_card(tmp_path).pictures}
    assert pics["dng1"].taken == "2026-10-18T21:43:05"
    w = Inspector()
    w.set_picture(pics["dng1"], None)
    assert _taken_lines(w) == ["Taken: 2026-10-18  21:43:05"]
    w.set_picture(pics["raf1"], None)
    assert _taken_lines(w) == ["Taken: 2026-01-02  03:04:05"]


def test_shows_when_the_preview_failed_or_is_loading(tmp_path):
    _jpg(tmp_path / "a.jpg", ORIG)
    pic = _scan_one(tmp_path)
    w = Inspector()
    w.set_picture(pic, None, reason="broken")
    assert _taken_lines(w) == ["Taken: 2026-10-18  21:43:05"]
    w.set_picture(pic, None)
    assert _taken_lines(w) == ["Taken: 2026-10-18  21:43:05"]


def test_line_sits_with_the_exif_rows(tmp_path):
    _jpg(tmp_path / "a.jpg", ORIG)
    w = Inspector()
    w.set_picture(_scan_one(tmp_path), None)
    rows = w.plain().splitlines()
    assert rows.index("Taken: 2026-10-18  21:43:05") > rows.index("ISO: --")
    assert rows[-1] == "Not marked"


def test_single_picture_card_still_reads_its_key(tmp_path):
    _jpg(tmp_path / "only.jpg", ORIG)
    assert _scan_one(tmp_path).taken == "2026-10-18T21:43:05"


def test_unset_camera_clock_is_absent(tmp_path):
    (tmp_path / "z.dng").write_bytes(tiff(b"0000:00:00 00:00:00"))
    pic = _scan_one(tmp_path)
    w = Inspector()
    w.set_picture(pic, None)
    assert _taken_lines(w)[0].endswith("(file date)") and "0000" not in _taken_lines(w)[0]
    from spuza.preview import PreviewInfo
    good = ex.Exif(taken="2026-10-18T21:43:05")
    w.set_picture(pic, PreviewInfo(pic.primary, "pillow", 8, 6, 100, 1.0, good))
    assert _taken_lines(w) == ["Taken: 2026-10-18  21:43:05"]
