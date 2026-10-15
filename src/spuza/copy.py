"""Copy the marked pictures to a folder, never overwriting or touching anything that exists.

Pure Python (no Qt): the UI calls ``plan_copy`` to show what will happen, then ``run_copy`` on a
worker thread. The only file this module ever removes is its own half-written ``.spuza-part`` temp.
"""

import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CHUNK = 1024 * 1024
MAX_SUFFIX = 10_000  # bound on the "-N" search for a free name


@dataclass
class CopyItem:
    src: Path
    dst: Path
    size: int
    action: str  # "copy" | "skip" | "rename"


@dataclass
class CopyPlan:
    dest: Path
    items: list[CopyItem] = field(default_factory=list)
    total_bytes: int = 0  # bytes that will actually be written
    pictures: int = 0


@dataclass
class CopyResult:
    copied: list[Path] = field(default_factory=list)  # source paths
    skipped: list[Path] = field(default_factory=list)  # source paths
    failed: list[tuple[Path, str]] = field(default_factory=list)
    bytes_copied: int = 0
    log_path: Path | None = None
    cancelled: bool = False


def human_size(n: int) -> str:
    """Decimal units like macOS Finder: 999 B, 602 KB, 1.2 GB."""
    if n < 1000:
        return f"{n} B"
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1000
        if value < 1000 or unit == "TB":
            break
    text = f"{value:.0f}" if value >= 100 else f"{value:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text} {unit}"


def free_space(dest: Path) -> int:
    """Free bytes on the volume holding ``dest`` (nearest existing parent if it is not there yet)."""
    p = Path(dest).absolute()
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


def _same_content(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ca, cb = fa.read(CHUNK), fb.read(CHUNK)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


def _suffixed(name: str, n: int) -> str:
    if n == 0:
        return name
    p = Path(name)
    return f"{p.stem}-{n}{p.suffix}"


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def plan_copy(groups: Sequence[Sequence[Path]], dest: Path) -> CopyPlan:
    """Decide, per file, copy / skip / rename. Creates nothing."""
    dest = Path(dest)
    plan = CopyPlan(dest=dest)
    taken: set[str] = set()  # names claimed by this plan (case-folded: Windows/macOS volumes)
    for group in groups:
        files = [Path(f) for f in group]
        if not files:
            continue
        plan.pictures += 1
        names = [f.name for f in files]
        # suffix 0: every file either free or an identical copy already there
        actions: list[str] | None = []
        for f in files:
            target = dest / f.name
            if f.name.casefold() in taken:
                actions = None
                break
            if not os.path.lexists(target):
                actions.append("copy")
            elif _same_content(f, target):
                actions.append("skip")
            else:
                actions = None
                break
        n = 0
        if actions is None:
            n = 1
            while n < MAX_SUFFIX and any(
                _suffixed(nm, n).casefold() in taken or os.path.lexists(dest / _suffixed(nm, n))
                for nm in names
            ):
                n += 1
            actions = ["rename"] * len(files)
        for f, act in zip(files, actions):
            name = _suffixed(f.name, n)
            taken.add(name.casefold())
            size = _size(f)
            plan.items.append(CopyItem(f, dest / name, size, act))
            if act != "skip":
                plan.total_bytes += size
    return plan


def _publish(tmp: Path, dst: Path) -> None:
    """Make tmp's bytes appear at dst, which must not exist; raises FileExistsError, never replaces."""
    try:
        os.link(tmp, dst)  # atomic and refuses to replace
    except FileExistsError:
        raise
    except OSError:
        # No hard links (exFAT/FAT cards): stream into dst opened O_EXCL, so an existing dst is never
        # replaced. If streaming fails, the half file under the final name is ours and is removed.
        with open(dst, "xb") as fout:
            try:
                with open(tmp, "rb") as fin:
                    shutil.copyfileobj(fin, fout, CHUNK)
            except BaseException:
                fout.close()
                try:
                    os.unlink(dst)
                except OSError:
                    pass
                raise
        shutil.copystat(tmp, dst)
    try:
        os.unlink(tmp)  # published already: a failure to tidy our temp is not a failed copy
    except OSError:
        pass


def _free_name(dst: Path) -> Path:
    for n in range(1, MAX_SUFFIX):
        cand = dst.with_name(_suffixed(dst.name, n))
        if not os.path.lexists(cand):
            return cand
    raise OSError(f"no free name for {dst.name}")


class _Cancelled(Exception):
    pass


def run_copy(
    plan: CopyPlan,
    progress: Callable[[int, int, Path], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    log_path: Path | None = None,
) -> CopyResult:
    res = CopyResult()
    total = plan.total_bytes
    done = 0
    notes: list[str] = []
    plan.dest.mkdir(parents=True, exist_ok=True)

    def report(src: Path) -> None:
        if progress:
            progress(done, total, src)

    def copy_one(item: CopyItem) -> Path:
        nonlocal done
        tmp = item.dst.with_name(f".{item.dst.name}.spuza-part")
        try:
            with open(item.src, "rb") as fin, open(tmp, "wb") as fout:
                while True:
                    if cancel and cancel():
                        raise _Cancelled
                    chunk = fin.read(CHUNK)
                    if not chunk:
                        break
                    fout.write(chunk)
                    done += len(chunk)
                    res.bytes_copied += len(chunk)
                    report(item.src)
            shutil.copystat(item.src, tmp)
            dst = item.dst
            while True:
                try:
                    _publish(tmp, dst)
                    break
                except FileExistsError:
                    dst = _free_name(item.dst)  # target appeared meanwhile: rename, never overwrite
            return dst
        except BaseException:
            try:
                os.unlink(tmp)  # our own temp, the only removal this module makes
            except OSError:
                pass
            raise

    for item in plan.items:
        if cancel and cancel():
            res.cancelled = True
            break
        if item.action == "skip":
            res.skipped.append(item.src)
            notes.append(f"SKIP   {item.src} (identical already at {item.dst})")
            continue
        before = done
        try:
            final = copy_one(item)
        except _Cancelled:
            res.bytes_copied -= done - before
            res.cancelled = True
            notes.append(f"CANCEL {item.src}")
            break
        except Exception as e:  # one bad file must not stop the rest
            res.bytes_copied -= done - before
            done = before + item.size  # keep progress heading to the total
            report(item.src)
            res.failed.append((item.src, f"{type(e).__name__}: {e}"))
            notes.append(f"FAIL   {item.src}: {type(e).__name__}: {e}")
            continue
        res.copied.append(item.src)
        tag = "RENAME" if item.action == "rename" or final != item.dst else "COPY  "
        notes.append(f"{tag} {item.src} -> {final}")

    if log_path is None:
        log_path = plan.dest / f"spuza-copy-{datetime.now():%Y%m%d-%H%M%S}.log"
    lines = [f"Spuza copy to {plan.dest}", *notes]
    lines.append(
        f"Totals: {len(res.copied)} copied, {len(res.skipped)} skipped, {len(res.failed)} failed, "
        f"{human_size(res.bytes_copied)} written" + (", CANCELLED" if res.cancelled else "")
    )
    try:
        res.log_path = _write_log(Path(log_path), "
".join(lines) + "
")
    except OSError:
        res.log_path = None
    return res


def _write_log(path: Path, text: str) -> Path:
    """Write text to a name that does not exist yet (path, else path-1, ...); never truncates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for n in range(MAX_SUFFIX):
        cand = path.with_name(_suffixed(path.name, n))
        try:
            with open(cand, "x", encoding="utf-8") as f:
                f.write(text)
            return cand
        except FileExistsError:
            continue
    raise OSError(f"no free log name for {path.name}")
