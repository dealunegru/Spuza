"""The picture list, the cursor and marks over it, and the background prefetcher.

Pure-Python parts: `scan`, `find_cards`, `Picture`, `Catalog`. Qt only enters in `Prefetcher`.

Pairing rule: within ONE directory, files sharing a stem (case-insensitive) group together. A RAW
(.raf/.dng) is the primary; the first JPEG (.jpg/.jpeg, by name) becomes its sidecar. With two raws on
one stem (RAF and DNG) they stay separate pictures and the JPEG pairs with the first raw by name.
Any further JPEG on the stem, and every other format (PNG, HEIC, ...), is its own picture.
"""

from __future__ import annotations

import glob
import os
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from spuza.ui import settings  # imports only QSettings; the defaults live in one place

# Same values as spuza.preview; duplicated so importing this module never imports the decoders.
RAW_EXTS = frozenset({".raf", ".dng"})
JPEG_EXTS = frozenset({".jpg", ".jpeg"})
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"})
KNOWN_EXTS = RAW_EXTS | IMAGE_EXTS

_SKIP_DIRS = {"system volume information", "$recycle.bin"}


@dataclass(frozen=True)
class Picture:
    stem: str
    primary: Path
    sidecar: Path | None
    files: tuple[Path, ...]
    kind: str
    size_bytes: int
    mtime: float


def _kind(ext: str, has_sidecar: bool) -> str:
    if ext in RAW_EXTS:
        return f"{ext[1:].upper()}+JPG" if has_sidecar else f"{ext[1:].upper()} only"
    return "JPG" if ext in JPEG_EXTS else ext[1:].upper()


def _hidden(name: str) -> bool:
    return name.startswith(".") or name.lower() in _SKIP_DIRS


def scan(root: Path) -> list[Picture]:
    """All pictures under `root`, in approximate capture order (mtime of primary, then name)."""
    # directory -> stem.lower() -> list of (name, ext, size, mtime)
    pictures: list[Picture] = []
    stack = [str(root)]
    while stack:
        d = stack.pop()
        groups: dict[str, list[tuple[str, str, int, float]]] = {}
        try:
            with os.scandir(d) as it:
                for e in it:
                    name = e.name
                    if _hidden(name):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                            continue
                        if e.is_symlink():
                            continue
                        stem, ext = os.path.splitext(name)
                        ext = ext.lower()
                        if ext not in KNOWN_EXTS:
                            continue
                        st = e.stat()
                    except OSError:
                        continue
                    groups.setdefault(stem.lower(), []).append((name, ext, st.st_size, st.st_mtime))
        except OSError:
            pass  # unreadable or failed mid-iteration: keep what was collected and pair it
        for members in groups.values():
            pictures.extend(_pair(d, members))
    pictures.sort(key=lambda p: (p.mtime, p.primary.name.lower(), str(p.primary.parent)))
    return pictures


def _pair(d: str, members: list[tuple[str, str, int, float]]) -> list[Picture]:
    members = sorted(members, key=lambda m: m[0].lower())
    raws = [m for m in members if m[1] in RAW_EXTS]
    jpegs = [m for m in members if m[1] in JPEG_EXTS]
    others = [m for m in members if m[1] not in RAW_EXTS and m[1] not in JPEG_EXTS]
    out: list[Picture] = []
    sidecar = None
    if raws and jpegs:
        sidecar = jpegs.pop(0)
    for i, (name, ext, size, mtime) in enumerate(raws):
        p = Path(d, name)
        side = sidecar if i == 0 else None
        files = (p,) + ((Path(d, side[0]),) if side else ())
        total = size + (side[2] if side else 0)
        out.append(Picture(os.path.splitext(name)[0], p, Path(d, side[0]) if side else None, files,
                           _kind(ext, side is not None), total, mtime))
    for name, ext, size, mtime in jpegs + others:
        p = Path(d, name)
        out.append(Picture(os.path.splitext(name)[0], p, None, (p,), _kind(ext, False), size, mtime))
    return out


def _glob_dirs(*patterns: str) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        out.extend(Path(p) for p in sorted(glob.glob(pat)) if os.path.isdir(p))
    return out


def _candidates() -> list[Path]:
    """Mount points that might be a camera card, per platform. Not filtered on DCIM yet."""
    if sys.platform == "darwin":
        return _glob_dirs("/Volumes/*")
    if sys.platform.startswith("win"):
        system = os.environ.get("SystemDrive", "C:").rstrip("\\/").upper()[:2]
        out = []
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = f"{letter}:"
            if drive == system:
                continue
            root = drive + "\\"
            if os.path.isdir(root):
                out.append(Path(root))
        return out
    return _glob_dirs("/media/*/*", "/run/media/*/*")


def find_cards(candidates: Iterable[Path] | None = None) -> list[Path]:
    """Mounted volumes whose top level holds a DCIM folder. `candidates` is for tests."""
    cands = _candidates() if candidates is None else candidates
    cards = []
    for c in cands:
        try:
            if (Path(c) / "DCIM").is_dir():
                cards.append(Path(c))
        except OSError:
            continue
    return cards


class Catalog:
    """Pictures plus a cursor and a set of marks. No Qt."""

    def __init__(self, pictures: Iterable[Picture]):
        self.pictures: list[Picture] = list(pictures)
        self.index = 0
        self._marks: set[int] = set()

    def __len__(self) -> int:
        return len(self.pictures)

    @property
    def current(self) -> Picture | None:
        return self.pictures[self.index] if self.pictures else None

    def next(self) -> bool:
        if self.index + 1 < len(self.pictures):
            self.index += 1
            return True
        return False

    def back(self) -> bool:
        if self.index > 0:
            self.index -= 1
            return True
        return False

    def go(self, i: int) -> None:
        self.index = max(0, min(int(i), len(self.pictures) - 1)) if self.pictures else 0

    def is_marked(self, i: int) -> bool:
        return i in self._marks

    def set_mark(self, i: int, on: bool) -> None:
        if not 0 <= i < len(self.pictures):
            return
        (self._marks.add if on else self._marks.discard)(i)

    def toggle_mark(self, i: int | None = None) -> bool:
        i = self.index if i is None else i
        if not 0 <= i < len(self.pictures):
            return False
        self.set_mark(i, i not in self._marks)
        return i in self._marks

    def marked(self) -> list[Picture]:
        return [self.pictures[i] for i in sorted(self._marks)]

    @property
    def marked_count(self) -> int:
        return len(self._marks)

    @property
    def marked_bytes(self) -> int:
        return sum(self.pictures[i].size_bytes for i in self._marks)

    def position_text(self) -> str:
        return f"{self.index + 1 if self.pictures else 0} of {len(self.pictures)}"


class _Job(QRunnable):
    def __init__(self, owner: Prefetcher, i: int, path: Path):
        super().__init__()
        self.owner, self.i, self.path = owner, i, path
        self.setAutoDelete(True)

    def run(self) -> None:
        o = self.owner
        if not o._wanted(self.i):
            o._done.emit(self.i, None, None, "", True)  # skipped before decoding
            return
        try:
            img, info = o._loader()(self.path, o.max_side)
            rgb = img.convert("RGB")
            w, h = rgb.size
            qimg = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        except Exception as exc:  # noqa: BLE001 - any decode failure is reported, never raised
            o._done.emit(self.i, None, None, str(exc) or type(exc).__name__, False)
            return
        o._done.emit(self.i, qimg, info, "", False)


class Prefetcher(QObject):
    """Decodes the pictures around the cursor on a thread pool; `cached()` never blocks."""

    ready = Signal(int, QImage, object)
    failed = Signal(int, str)
    _done = Signal(int, object, object, str, bool)

    def __init__(self, catalog: Catalog, ahead: int = settings.DEFAULT_AHEAD,
                 behind: int = settings.DEFAULT_BEHIND, max_side: int = 2560,
                 loader: Callable | None = None, pool: QThreadPool | None = None,
                 threads: int = settings.DEFAULT_THREADS):
        super().__init__()
        self.catalog, self.ahead, self.behind, self.max_side = catalog, ahead, behind, max_side
        self._loader_fn = loader
        self._own_pool = pool is None
        self.pool = pool or QThreadPool()
        if self._own_pool:
            self.pool.setMaxThreadCount(threads)
        self._cache: dict[int, QImage] = {}
        self._info: dict[int, object] = {}
        self._inflight: set[int] = set()
        self._failed: dict[int, str] = {}
        self._window = (0, -1)
        self._closed = False
        self._lock = threading.Lock()
        self._done.connect(self._on_done)

    def _loader(self) -> Callable:
        if self._loader_fn is None:
            from spuza.preview import load  # lazy: keeps decoders out of module import

            self._loader_fn = load
        return self._loader_fn

    def _wanted(self, i: int) -> bool:
        lo, hi = self._window
        return not self._closed and lo <= i <= hi

    def cached(self, i: int) -> QImage | None:
        return self._cache.get(i)

    def info(self, i: int):
        return self._info.get(i)

    def failed_reason(self, i: int) -> str | None:
        return self._failed.get(i)

    def ready_ahead(self) -> int:
        n, i = 0, self.catalog.index + 1
        while i in self._cache:
            n += 1
            i += 1
        return n

    def update(self, i: int) -> None:
        if self._closed:
            return
        last = len(self.catalog) - 1
        lo, hi = max(0, i - self.behind), min(last, i + self.ahead)
        self._window = (lo, hi)
        for k in [k for k in self._cache if not lo <= k <= hi]:
            del self._cache[k]
            self._info.pop(k, None)
        for k in [k for k in self._failed if not lo <= k <= hi]:
            del self._failed[k]  # left the window: a fresh visit may retry once
        order = [i] + list(range(i + 1, hi + 1)) + list(range(i - 1, lo - 1, -1))
        for k in order:
            if lo <= k <= hi:
                self._start(k)

    def set_window(self, ahead: int, behind: int) -> None:
        """Resize the window live and re-centre on the cursor. GUI thread only (a dialog's OK calls it).

        Negative sizes are clamped to 0; the caller (the prefs sheet, the CLI) enforces the user-facing
        ranges. Entries outside the new window are dropped, new in-window jobs start. No-op after shutdown.
        """
        if self._closed:
            return
        self.ahead, self.behind = max(0, int(ahead)), max(0, int(behind))
        self.update(self.catalog.index)

    def _start(self, k: int) -> None:
        if k in self._cache or k in self._inflight or k in self._failed or self._closed:
            return
        self._inflight.add(k)
        self.pool.start(_Job(self, k, self.catalog.pictures[k].primary))

    def _on_done(self, i: int, img, info, err: str, skipped: bool) -> None:
        self._inflight.discard(i)
        if self._closed:
            return
        if not self._wanted(i):
            return  # stale generation: discard, do not emit
        if skipped:
            self._start(i)  # skipped while outside the window, but it is wanted again now
        elif err:
            self._failed[i] = err
            self.failed.emit(i, err)
        else:
            self._cache[i] = img
            self._info[i] = info
            self.ready.emit(i, img, info)

    def shutdown(self, timeout_ms: int = 5000) -> None:
        self._closed = True
        self.pool.clear()
        self.pool.waitForDone(timeout_ms)
