"""A real Prefetcher walking a synthetic 1000-JPEG card keeps memory flat and the pool bounded.

RSS source per platform (stdlib only): Windows GetProcessMemoryInfo WorkingSetSize via ctypes; Linux
/proc/self/statm resident pages; macOS mach task_info(MACH_TASK_BASIC_INFO) resident_size via ctypes,
falling back to resource.getrusage ru_maxrss (a peak, not current, so the growth bounds stay valid only
as upper limits there).
"""

import ctypes
import os
import shutil
import sys
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QCoreApplication

from spuza.catalog import Catalog, Prefetcher, scan
from spuza.ui import settings

CARD_SIZE = 1000
STEPS = 300
STEP_TIMEOUT_S = 30.0
GROWTH_BOUND_MB = 60  # 150 -> 300: a leak of one preview (13 MB) per ~4 steps would blow this
SLACK_MB = 120  # decoder arenas, allocator fragmentation, Qt/PIL import cost on top of the window


def rss_mb() -> float:
    if sys.platform.startswith("win"):
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        assert psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.WorkingSetSize / 1e6
    if sys.platform.startswith("linux"):
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    if sys.platform == "darwin":
        try:
            libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")

            class Basic(ctypes.Structure):
                _fields_ = [("virtual_size", ctypes.c_uint64), ("resident_size", ctypes.c_uint64),
                            ("resident_size_max", ctypes.c_uint64), ("user_time", ctypes.c_uint64 * 1),
                            ("system_time", ctypes.c_uint64 * 1), ("policy", ctypes.c_int),
                            ("suspend_count", ctypes.c_int)]

            info = Basic()
            count = ctypes.c_uint32(ctypes.sizeof(Basic) // 4)
            libc.mach_task_self.restype = ctypes.c_uint32
            MACH_TASK_BASIC_INFO = 20
            kr = libc.task_info(libc.mach_task_self(), MACH_TASK_BASIC_INFO, ctypes.byref(info),
                                ctypes.byref(count))
            if kr == 0 and info.resident_size:
                return info.resident_size / 1e6
        except Exception:  # noqa: BLE001 - fall through to getrusage
            pass
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3  # bytes on macOS, KiB elsewhere


def make_card(root: Path) -> None:
    root.mkdir()
    base = root / "IMG_0000.JPG"
    # a gradient plus blocky noise: compressible like a photo, not a flat colour
    img = Image.effect_noise((3000, 2000), 40).convert("RGB")
    grad = Image.linear_gradient("L").resize((3000, 2000)).convert("RGB")
    Image.blend(img, grad, 0.5).save(base, quality=85)
    for i in range(1, CARD_SIZE):
        dst = root / f"IMG_{i:04d}.JPG"
        try:
            os.link(base, dst)
        except OSError:
            shutil.copy(base, dst)


def pump(cond, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.001)
    return False


def test_walk_1000_card_memory_flat(tmp_path, capsys):
    t_start = time.monotonic()
    make_card(tmp_path / "card")
    cat = Catalog(scan(tmp_path / "card"))
    assert len(cat) == CARD_SIZE
    pf = Prefetcher(cat, ahead=12, behind=8)  # real spuza.preview.load
    try:
        assert pf.pool.maxThreadCount() <= 4
        before = rss_mb()
        mid = end = 0.0
        for i in range(STEPS + 1):
            cat.go(i)
            pf.update(i)
            assert pump(lambda i=i: pf.cached(i) is not None, STEP_TIMEOUT_S), f"step {i} never cached"
            assert len(pf._cache) <= 12 + 8 + 1
            if i == 150:
                mid = rss_mb()
            if i == STEPS:
                end = rss_mb()
    finally:
        pf.shutdown()
    window = settings.window_mb(12, 8)
    with capsys.disabled():
        print(f"\nRSS before={before:.0f} MB at150={mid:.0f} MB at300={end:.0f} MB "
              f"window_mb={window} wall={time.monotonic() - t_start:.1f}s")
    assert end - mid < GROWTH_BOUND_MB
    # The plateau is above the preview bytes on macOS and Linux: CI run 38875388229 measured +464 and
    # +465 MB for a 275 MB window (worker-thread malloc arenas keep freed decode buffers; Windows trims
    # its working set to about +190). Flatness above is the leak check; this bound only catches a
    # window that is wildly bigger than configured (a leak of one preview per 4 steps would be +975).
    assert end - before < 2 * window + SLACK_MB
