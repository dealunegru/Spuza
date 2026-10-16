"""Tests for spuza.catalog: scan/pairing, card discovery, Catalog state, Prefetcher."""

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QThreadPool
from PySide6.QtGui import QGuiApplication, QImage

from spuza import catalog
from spuza.catalog import Catalog, Picture, Prefetcher


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance() or QGuiApplication([])
    yield app


def touch(p: Path, data: bytes = b"", mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def names(pics):
    return [p.primary.name for p in pics]


# ---- scan ----------------------------------------------------------------------------------


def test_pairing_is_case_insensitive_and_sums_size(tmp_path):
    raw = touch(tmp_path / "X.RAF", b"12345", 100)
    jpg = touch(tmp_path / "x.jpg", b"123", 100)
    (pic,) = catalog.scan(tmp_path)
    assert pic.primary == raw and pic.sidecar == jpg and pic.files == (raw, jpg)
    assert pic.kind == "RAF+JPG" and pic.size_bytes == 8 and pic.stem.lower() == "x"


@pytest.mark.parametrize(
    "files,kind",
    [
        (["a.RAF"], "RAF only"),
        (["a.dng"], "DNG only"),
        (["a.DNG", "a.JPEG"], "DNG+JPG"),
        (["a.jpeg"], "JPG"),
        (["a.png"], "PNG"),
        (["a.heic"], "HEIC"),
        (["a.tif"], "TIF"),
        (["a.tiff"], "TIFF"),
        (["a.gif"], "GIF"),
        (["a.bmp"], "BMP"),
    ],
)
def test_kinds(tmp_path, files, kind):
    for f in files:
        touch(tmp_path / f, b"x")
    (pic,) = catalog.scan(tmp_path)
    assert pic.kind == kind


def test_lone_jpeg_has_no_sidecar(tmp_path):
    touch(tmp_path / "a.jpg", b"x")
    (pic,) = catalog.scan(tmp_path)
    assert pic.sidecar is None and pic.files == (pic.primary,)


def test_same_stem_in_different_dirs_not_paired(tmp_path):
    touch(tmp_path / "d1" / "a.RAF", b"x")
    touch(tmp_path / "d2" / "a.jpg", b"x")
    assert sorted(p.kind for p in catalog.scan(tmp_path)) == ["JPG", "RAF only"]


def test_two_raws_one_stem_jpeg_pairs_with_first_by_name(tmp_path):
    touch(tmp_path / "a.RAF", b"x", 5)
    touch(tmp_path / "a.DNG", b"x", 5)
    touch(tmp_path / "a.jpg", b"x", 5)
    pics = catalog.scan(tmp_path)
    by = {p.primary.suffix.lower(): p for p in pics}
    assert len(pics) == 2
    assert by[".dng"].sidecar is not None and by[".raf"].sidecar is None


def test_png_and_jpg_same_stem_are_separate(tmp_path):
    touch(tmp_path / "a.png", b"x")
    touch(tmp_path / "a.jpg", b"x")
    assert len(catalog.scan(tmp_path)) == 2


def test_skips_hidden_unknown_and_system_dirs(tmp_path):
    touch(tmp_path / "ok.jpg", b"x")
    touch(tmp_path / "._ok.jpg", b"x")
    touch(tmp_path / ".hidden.jpg", b"x")
    touch(tmp_path / "notes.txt", b"x")
    for d in (".Trashes", ".Spotlight-V100", ".fseventsd", "System Volume Information", "$RECYCLE.BIN"):
        touch(tmp_path / d / "bad.jpg", b"x")
    assert names(catalog.scan(tmp_path)) == ["ok.jpg"]


def test_order_is_mtime_then_name_and_recursive(tmp_path):
    touch(tmp_path / "b.jpg", b"x", 100)
    touch(tmp_path / "a.jpg", b"x", 200)
    touch(tmp_path / "sub" / "c.jpg", b"x", 100)
    assert names(catalog.scan(tmp_path)) == ["b.jpg", "c.jpg", "a.jpg"]


def test_scan_missing_or_empty_root(tmp_path):
    assert catalog.scan(tmp_path / "nope") == []
    assert catalog.scan(tmp_path) == []


def test_symlink_loop_terminates(tmp_path):
    touch(tmp_path / "a.jpg", b"x")
    try:
        os.symlink(tmp_path, tmp_path / "loop", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert names(catalog.scan(tmp_path)) == ["a.jpg"]


def test_scan_5000_files_fast(tmp_path):
    for d in range(10):
        (tmp_path / f"d{d}").mkdir()
        for n in range(500):
            (tmp_path / f"d{d}" / f"IMG_{n:04d}.jpg").write_bytes(b"")
    t = time.perf_counter()
    pics = catalog.scan(tmp_path)
    dt = time.perf_counter() - t
    print(f"scan 5000 files: {dt:.3f}s")
    assert len(pics) == 5000
    assert dt < 3.0  # target ~1 s; generous ceiling so a loaded box does not flake


def test_real_set(real_set):
    pics = catalog.scan(real_set)
    assert len(pics) == 6
    assert sorted(p.kind for p in pics) == ["DNG only"] * 5 + ["RAF only"]


# ---- find_cards ----------------------------------------------------------------------------


def test_find_cards_filters_on_dcim(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    (a / "DCIM").mkdir(parents=True)
    b.mkdir()
    assert catalog.find_cards(candidates=[a, b, tmp_path / "missing"]) == [a]


def test_candidates_windows_skips_system_drive(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemDrive", "C:")
    monkeypatch.setattr(catalog.os.path, "isdir", lambda p: str(p)[:1] in "CDE")
    got = [str(c)[:1] for c in catalog._candidates()]
    assert "C" not in got and "D" in got and "E" in got


def test_candidates_darwin_uses_volumes(monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(catalog, "_glob_dirs", lambda *pats: seen.extend(pats) or [])
    assert catalog._candidates() == []
    assert seen == ["/Volumes/*"]


def test_candidates_linux_patterns(monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(catalog, "_glob_dirs", lambda *pats: seen.extend(pats) or [])
    catalog._candidates()
    assert seen == ["/media/*/*", "/run/media/*/*"]


# ---- Catalog -------------------------------------------------------------------------------


def pic(i, size=10):
    p = Path(f"/x/{i}.jpg")
    return Picture(str(i), p, None, (p,), "JPG", size, float(i))


def test_catalog_empty():
    c = Catalog([])
    assert (c.index, c.current, len(c), c.position_text()) == (0, None, 0, "0 of 0")
    assert not c.next() and not c.back()
    assert c.marked() == [] and c.marked_count == 0 and c.marked_bytes == 0
    c.go(5)
    assert c.index == 0


def test_catalog_navigation_and_clamp():
    c = Catalog([pic(i) for i in range(3)])
    assert c.position_text() == "1 of 3" and not c.back()
    assert c.next() and c.next() and not c.next()
    assert c.index == 2 and c.current.stem == "2"
    c.go(99)
    assert c.index == 2
    c.go(-4)
    assert c.index == 0
    assert c.pictures[1].stem == "1"


def test_catalog_marks():
    c = Catalog([pic(i, size=i + 1) for i in range(4)])
    assert c.toggle_mark() is True and c.is_marked(0)
    c.go(2)
    assert c.toggle_mark() is True
    assert c.toggle_mark(3) is True and c.toggle_mark(3) is False
    c.set_mark(1, True)
    c.set_mark(0, False)
    assert [p.stem for p in c.marked()] == ["1", "2"]
    assert c.marked_count == 2 and c.marked_bytes == 2 + 3
    assert not c.is_marked(99) and not c.is_marked(-1)


# ---- Prefetcher ----------------------------------------------------------------------------


def fake_loader(log=None):
    def load(path, max_side):
        if log is not None:
            log.append(Path(path).name)
        return Image.new("RGB", (8, 6), (10, 20, 30)), None

    return load


def pump(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.002)
    return False


@pytest.fixture
def make_pf(qapp):
    made = []

    def make(n=20, **kw):
        cat = Catalog([pic(i) for i in range(n)])
        pf = Prefetcher(cat, **kw)
        made.append(pf)
        return cat, pf

    yield make
    for pf in made:
        pf.shutdown()


def test_prefetch_window_fills_and_evicts(make_pf):
    cat, pf = make_pf(loader=fake_loader(), ahead=4, behind=2)
    got = []
    pf.ready.connect(lambda i, img, info: got.append(i))
    cat.go(5)
    pf.update(5)
    assert pf.cached(5) is None  # non-blocking: nothing decoded synchronously
    assert pump(lambda: all(pf.cached(i) is not None for i in range(3, 10)))
    img = pf.cached(5)
    assert isinstance(img, QImage) and (img.width(), img.height()) == (8, 6)
    assert pf.ready_ahead() == 4
    assert set(got) == set(range(3, 10))
    pf.update(15)
    assert pf.cached(5) is None
    assert pump(lambda: pf.cached(15) is not None)


def test_prefetch_order_current_first(make_pf):
    log = []
    one = QThreadPool()
    one.setMaxThreadCount(1)  # a single worker makes start order observable
    cat, pf = make_pf(loader=fake_loader(log), ahead=2, behind=1, pool=one)
    pf.update(5)
    assert pump(lambda: len(log) >= 4)
    assert log[:4] == ["5.jpg", "6.jpg", "7.jpg", "4.jpg"]


def test_prefetch_failed_signal(make_pf):
    def bad(path, max_side):
        raise RuntimeError("boom")

    cat, pf = make_pf(loader=bad, ahead=0, behind=0)
    errs = []
    pf.failed.connect(lambda i, m: errs.append((i, m)))
    pf.update(2)
    assert pump(lambda: errs)
    assert errs[0][0] == 2 and "boom" in errs[0][1]
    assert pf.cached(2) is None


def test_stale_result_dropped_and_no_double_decode(make_pf):
    gate = threading.Event()
    started = threading.Event()
    calls = []
    lock = threading.Lock()

    def slow(path, max_side):
        with lock:
            calls.append(Path(path).name)
        if Path(path).name == "0.jpg":
            started.set()
            gate.wait(5)
        return Image.new("RGB", (4, 4)), None

    cat, pf = make_pf(n=40, loader=slow, ahead=1, behind=0)
    got = []
    pf.ready.connect(lambda i, img, info: got.append(i))
    pf.update(0)
    assert started.wait(5)
    pf.update(0)  # same window while 0 is decoding: must not decode it twice
    pf.update(30)  # window moves away; 0 is now stale
    gate.set()
    assert pump(lambda: pf.cached(30) is not None)
    pump(lambda: False, timeout=0.2)
    assert 0 not in got and pf.cached(0) is None
    assert calls.count("0.jpg") == 1


def test_ready_ahead_counts_contiguous_only(make_pf):
    cat, pf = make_pf(loader=fake_loader(), ahead=3, behind=0)
    pf.update(0)
    assert pump(lambda: pf.ready_ahead() == 3)
    pf._cache.pop(2)
    assert pf.ready_ahead() == 1


def test_default_loader_is_lazy(monkeypatch, qapp):
    monkeypatch.setitem(sys.modules, "spuza.preview", None)
    pf = Prefetcher(Catalog([]))  # constructing must not import spuza.preview
    pf.shutdown()


def test_failed_index_is_not_retried_inside_window(make_pf):
    calls = []

    def flaky(path, max_side):
        calls.append(Path(path).name)
        if Path(path).name == "5.jpg":
            raise RuntimeError("corrupt")
        return Image.new("RGB", (4, 4)), None

    cat, pf = make_pf(loader=flaky, ahead=3, behind=1)
    errs = []
    pf.failed.connect(lambda i, m: errs.append(i))
    cat.go(5)
    for _ in range(4):
        pf.update(5)
        assert pump(lambda: all(pf.cached(i) is not None for i in (4, 6, 7, 8)))
        pump(lambda: False, timeout=0.05)
    pf.update(6)
    pump(lambda: False, timeout=0.1)
    assert calls.count("5.jpg") == 1 and errs == [5]
    assert pf.failed_reason(5) == "corrupt" and pf.failed_reason(6) is None
    pf.update(15)  # 5 leaves the window: forgotten, a fresh visit may retry once
    assert pf.failed_reason(5) is None
    assert pump(lambda: pf.cached(15) is not None)
    pf.update(5)
    assert pump(lambda: calls.count("5.jpg") == 2)
