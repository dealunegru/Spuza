"""scan_card orders pictures by the camera's capture key; unkeyed pictures follow, by mtime."""

import os

import pytest

from spuza import catalog
from spuza.catalog import scan, scan_card

from conftest import DNG_NAMES, REAL_SET
from test_capture import jpeg, tiff


def _jpg(root, name, date, mtime, subsec=None):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(jpeg(tiff(date=date, subsec=subsec)))
    os.utime(p, (mtime, mtime))
    return p


def _plain(root, name, mtime):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"not an image with metadata")
    os.utime(p, (mtime, mtime))
    return p


def _names(root, **kw):
    return [p.primary.name for p in scan_card(root, **kw).pictures]


@pytest.mark.skipif(not (REAL_SET / DNG_NAMES[0]).exists(), reason="real jes set not present")
def test_jes_dngs_list_146_to_150():
    got = [n for n in _names(REAL_SET) if n.endswith(".DNG")]
    assert got == DNG_NAMES


@pytest.mark.skipif(not (REAL_SET / DNG_NAMES[0]).exists(), reason="real jes set not present")
def test_jes_mtime_escape_hatch_keeps_old_order():
    got = [n for n in _names(REAL_SET, order="mtime") if n.endswith(".DNG")]
    assert got != DNG_NAMES


def test_capture_beats_reversed_mtime(tmp_path):
    _jpg(tmp_path, "a.jpg", b"2026:10:15 12:00:03", 100)
    _jpg(tmp_path, "b.jpg", b"2026:10:15 12:00:01", 300)
    _jpg(tmp_path, "c.jpg", b"2026:10:15 12:00:02", 200)
    assert _names(tmp_path) == ["b.jpg", "c.jpg", "a.jpg"]
    assert _names(tmp_path, order="mtime") == ["a.jpg", "c.jpg", "b.jpg"]


def test_subsec_and_same_key_falls_to_name_then_folder(tmp_path):
    _jpg(tmp_path, "z.jpg", b"2026:10:15 12:00:01", 1, subsec=b"05")
    _jpg(tmp_path, "y.jpg", b"2026:10:15 12:00:01", 2, subsec=b"50")
    _jpg(tmp_path, "d2/same.jpg", b"2026:10:15 12:00:09", 3)
    _jpg(tmp_path, "d1/same.jpg", b"2026:10:15 12:00:09", 4)
    pics = scan(tmp_path)
    assert [p.primary.name for p in pics] == ["z.jpg", "y.jpg", "same.jpg", "same.jpg"]
    assert [p.primary.parent.name for p in pics[2:]] == ["d1", "d2"]


def test_unkeyed_fall_back_to_mtime_after_keyed(tmp_path):
    _plain(tmp_path, "late.jpg", 900)
    _plain(tmp_path, "early.jpg", 100)
    _jpg(tmp_path, "keyed.jpg", b"2026:10:15 12:00:01", 5000)
    assert _names(tmp_path) == ["keyed.jpg", "early.jpg", "late.jpg"]


def test_all_unkeyed_is_the_old_mtime_order(tmp_path):
    for n, t in (("a.jpg", 30), ("b.jpg", 10), ("c.jpg", 20)):
        _plain(tmp_path, n, t)
    assert _names(tmp_path) == _names(tmp_path, order="mtime") == ["b.jpg", "c.jpg", "a.jpg"]


def test_capture_key_raising_or_none_means_unkeyed(tmp_path, monkeypatch):
    _jpg(tmp_path, "a.jpg", b"2026:10:15 12:00:02", 200)
    _jpg(tmp_path, "b.jpg", b"2026:10:15 12:00:01", 100)
    _jpg(tmp_path, "c.jpg", b"2026:10:15 12:00:00", 300)

    def boom(path):
        raise RuntimeError("read failed")
    monkeypatch.setattr(catalog, "capture_key", boom)
    assert _names(tmp_path) == ["b.jpg", "a.jpg", "c.jpg"]  # mtime order
    monkeypatch.setattr(catalog, "capture_key", lambda path: None)
    assert _names(tmp_path) == ["b.jpg", "a.jpg", "c.jpg"]


def test_one_failing_read_only_unkeys_that_file(tmp_path, monkeypatch):
    real = catalog.capture_key
    _jpg(tmp_path, "a.jpg", b"2026:10:15 12:00:02", 200)
    _jpg(tmp_path, "b.jpg", b"2026:10:15 12:00:01", 100)
    _jpg(tmp_path, "c.jpg", b"2026:10:15 12:00:00", 300)

    def flaky(path):
        if path.name == "c.jpg":
            raise OSError("gone")
        return real(path)
    monkeypatch.setattr(catalog, "capture_key", flaky)
    assert _names(tmp_path) == ["b.jpg", "a.jpg", "c.jpg"]


def test_raw_jpeg_pair_keyed_by_primary(tmp_path):
    for stem, d in (("p1", b"2026:10:15 12:00:02"), ("p2", b"2026:10:15 12:00:01")):
        _jpg(tmp_path, stem + ".jpg", d, 10 if stem == "p1" else 20)
    assert _names(tmp_path) == ["p2.jpg", "p1.jpg"]


def test_reads_are_bounded_and_threaded(tmp_path, monkeypatch):
    import threading
    seen, peak, now = set(), [0], [0]
    lock = threading.Lock()
    real = catalog.capture_key

    def watch(path):
        with lock:
            seen.add(threading.get_ident())
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        try:
            return real(path)
        finally:
            with lock:
                now[0] -= 1
    monkeypatch.setattr(catalog, "capture_key", watch)
    for i in range(60):
        _jpg(tmp_path, f"f{i:02d}.jpg", b"2026:10:15 12:00:%02d" % i, 1000 - i)
    assert _names(tmp_path) == [f"f{i:02d}.jpg" for i in range(60)]
    assert threading.get_ident() not in seen
    assert peak[0] <= catalog.KEY_WORKERS
    assert catalog.KEY_WORKERS <= 8


def test_bad_order_value_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        scan_card(tmp_path, order="nope")
