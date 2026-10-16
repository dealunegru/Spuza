"""The picture list, the cursor and marks over it, and the background prefetcher.

Pure-Python parts: `scan`, `scan_card`, `find_cards`, `Picture`, `Catalog`. Qt only enters in `Prefetcher`.

Pairing rule: within ONE directory, files sharing a stem (case-insensitive) group together. A RAW
(.raf/.dng/.arw/.cr3/...) is the primary; the first JPEG (.jpg/.jpeg, by name) becomes its sidecar, and a
HEIF (.hif) on the stem joins the same picture (it is the sidecar only when there is no JPEG). With two
raws on one stem they stay separate pictures and the JPEG/HIF pair with the first raw by name. Any further
JPEG or HIF on the stem, and every other format (PNG, HEIC, a lone HIF, ...), is its own picture.
Every other file on the stem (Live Photo MOV, XMP, THM, WAV, any unknown extension) is a companion of the
stem's first picture: it rides in `files` and `companions` and counts in `size_bytes`, but is never a
picture. Non-picture files on no picture's stem are reported as `others`, never dropped.
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
RAW_EXTS = frozenset({".raf", ".dng", ".arw", ".nef", ".nrw", ".pef", ".3fr", ".cr2", ".orf", ".rw2", ".cr3"})
JPEG_EXTS = frozenset({".jpg", ".jpeg"})
HEIF_EXTS = frozenset({".hif", ".heif", ".heic"})
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff"}) | HEIF_EXTS
KNOWN_EXTS = RAW_EXTS | IMAGE_EXTS

_SKIP_DIRS = {"system volume information", "$recycle.bin"}

_Member = tuple[str, str, int, float]  # name, lowercase ext, size, mtime


@dataclass(frozen=True)
class Picture:
    stem: str
    primary: Path
    sidecar: Path | None
    files: tuple[Path, ...]
    kind: str
    size_bytes: int
    mtime: float
    companions: tuple[Path, ...] = ()  # non-picture files on the same stem (XMP, THM, Live Photo MOV, ...); also in `files`
    twins: tuple[Path, ...] = ()  # the same files on a second card (dual-slot backup); never copied, only reported


@dataclass(frozen=True)
class ScanResult:
    pictures: list[Picture]
    others: tuple[Path, ...]  # non-picture files on no picture's stem: counted and reported
    unreadable: tuple[Path, ...]  # entries whose is_dir/stat raised OSError (and directories that could not be listed)


def _kind(ext: str, has_jpeg: bool, has_hif: bool = False) -> str:
    if ext in RAW_EXTS:
        parts = [ext[1:].upper()] + (["JPG"] if has_jpeg else []) + (["HIF"] if has_hif else [])
        return "+".join(parts) if len(parts) > 1 else f"{parts[0]} only"
    return "JPG" if ext in JPEG_EXTS else ext[1:].upper()


def _hidden(name: str) -> bool:
    return name.startswith(".") or name.lower() in _SKIP_DIRS


def scan(root: Path) -> list[Picture]:
    """All pictures under `root`, in approximate capture order (mtime of primary, then name)."""
    return scan_card(root).pictures


def scan_card(root: Path) -> ScanResult:
    """Walk `root`: the pictures (by mtime of primary, then name), the other files, the unreadable entries.

    Skipped without report, as before: dot-files and dot-directories, `System Volume Information`,
    `$RecycleBin`, and symlinks. Everything else is a picture, a companion, an "other", or unreadable.
    """
    pictures: list[Picture] = []
    others: list[Path] = []
    unreadable: list[Path] = []
    stack = [str(root)]
    while stack:
        d = stack.pop()
        groups: dict[str, list[_Member]] = {}
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
                        st = e.stat()
                    except OSError:
                        unreadable.append(Path(d, name))
                        continue
                    groups.setdefault(stem.lower(), []).append((name, ext.lower(), st.st_size, st.st_mtime))
        except OSError:
            unreadable.append(Path(d))  # failed to list or failed mid-iteration: pair what was collected
        for members in groups.values():
            pics, rest = _pair(d, members)
            pictures.extend(pics)
            others.extend(rest)
    pictures.sort(key=lambda p: (p.mtime, p.primary.name.lower(), str(p.primary.parent)))
    return ScanResult(pictures, tuple(sorted(others, key=str)), tuple(sorted(unreadable, key=str)))


def _pair(d: str, members: list[_Member]) -> tuple[list[Picture], list[Path]]:
    """One directory's one stem -> (its pictures, its non-picture files when no picture owns them)."""
    members = sorted(members, key=lambda m: m[0].lower())
    raws = [m for m in members if m[1] in RAW_EXTS]
    jpegs = [m for m in members if m[1] in JPEG_EXTS]
    hifs = [m for m in members if m[1] == ".hif"]
    companions = [m for m in members if m[1] not in KNOWN_EXTS]
    jpeg_side = jpegs.pop(0) if raws and jpegs else None
    hif_side = hifs.pop(0) if raws and hifs else None
    sidecar = jpeg_side or hif_side
    singles = [m for m in members if m in jpegs or m in hifs
               or (m[1] in IMAGE_EXTS and m[1] not in JPEG_EXTS and m[1] != ".hif")]
    out: list[Picture] = []
    for i, (name, ext, size, mtime) in enumerate(raws):
        p = Path(d, name)
        sides = [m for m in (jpeg_side, hif_side) if m] if i == 0 else []
        out.append(Picture(os.path.splitext(name)[0], p, Path(d, sidecar[0]) if sides and sidecar else None,
                           (p,) + tuple(Path(d, m[0]) for m in sides),
                           _kind(ext, bool(sides and jpeg_side), bool(sides and hif_side)),
                           size + sum(m[2] for m in sides), mtime))
    for name, ext, size, mtime in singles:
        p = Path(d, name)
        out.append(Picture(os.path.splitext(name)[0], p, None, (p,), _kind(ext, False), size, mtime))
    if not out:
        return [], [Path(d, m[0]) for m in companions]
    comp = tuple(Path(d, m[0]) for m in companions)
    if comp:
        f = out[0]
        out[0] = Picture(f.stem, f.primary, f.sidecar, f.files + comp, f.kind,
                         f.size_bytes + sum(m[2] for m in companions), f.mtime, comp, f.twins)
    return out, []


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

    def __init__(self, pictures: Iterable[Picture], others: Iterable[Path] = (), unreadable: Iterable[Path] = ()):
        self.pictures: list[Picture] = list(pictures)
        self.others: list[Path] = list(others)  # files on no picture's stem that are not pictures: counted, reported
        self.unreadable: list[Path] = list(unreadable)  # entries the scan could not classify (stat failed): reported
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

    def busy(self) -> bool:
        """True while any decode is queued or running (the window waits for False before importing numpy)."""
        return not self._closed and bool(self._inflight)  # shutdown drops queued jobs without a signal

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
