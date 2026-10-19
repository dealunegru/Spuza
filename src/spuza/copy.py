"""Copy the marked pictures to a folder, never overwriting or touching anything that exists.

Plain Python, no widgets (it imports spuza.catalog for the extension sets, which loads Qt core): the UI calls ``plan_copy`` to show what will happen, then ``run_copy`` on a
worker thread. The only file this module ever removes is its own half-written ``.spuza-part`` temp.

Crops: ``plan_copy(crops=[CropJob, ...])`` adds, per cropped picture, one JPEG at ``<dest>/ed/<stem><suffix>.jpg``
(organised or flat layout alike, so it never shares a folder with an original). The suffix is
``ratio.export_suffix(job.ratio or FREE, job.turns)``: none for Free and Original (``<stem>.jpg``), else the ratio as
seen on screen, ``_4x5``, ``_5x4`` (turned or an odd number of user turns), ``_1x1``, ``_3x4``, ``_2x3``, ``_9x16``,
``_191x100`` / ``_100x191``. A picture may have a crop AND a story: both are written, as two files in ed
(``<stem>[_4x5].jpg`` and ``<stem>_story.jpg``). The originals' plan is untouched: crops live in
``plan.crop_items``, and ``plan.total_bytes`` adds an ESTIMATE for them (``estimate_crop_bytes``). A name taken in ed
(by a file already there or by an earlier crop of this plan) gets the usual ``-N`` suffix; an existing result is never
recognised as identical, so a repeated copy always writes a new suffixed file (identity of a re-encoded JPEG cannot be
proven cheaply). Each crop is rendered on the worker, written to a ``.spuza-part`` temp and published without replacing.

Stories: ``plan_copy(stories=[StoryJob, ...])`` adds, per picture, one 1080x1920 JPEG at ``<dest>/ed/<stem>_story.jpg``
(``plan.story_items``, action "story"), under the same rules as crops (same name-clash and publish path, run after
the originals and the crops, turns always baked in). Its size is a fixed estimate, ``STORY_BYTES``.
"""

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from spuza import crop as crop_mod
from spuza import preview, rotation
from spuza import ratio as ratio_mod
from spuza import story as story_mod
from spuza.catalog import KNOWN_EXTS, RAW_EXTS

CHUNK = 1024 * 1024
MAX_SUFFIX = 10_000  # bound on the "-N" search for a free name
RAW_DIR, SOC_DIR, ED_DIR = "raw", "soc", "ed"  # the folders an organised copy makes in the destination
ENV_LOGS = "SPUZA_LOGS_DIR"  # overrides logs_dir(); tests set it so they never touch real app data


@dataclass
class CopyItem:
    src: Path
    dst: Path
    size: int
    action: str  # "copy" | "skip" | "rename"
    turns: int = 0  # clockwise quarter turns to write into this copy as EXIF orientation (0: none)
    crop_job: crop_mod.CropJob | None = None  # set on a crop item (action "crop"): src is the primary, dst is in ed
    story_job: story_mod.StoryJob | None = None  # set on a story item (action "story"), likewise


@dataclass
class CopyPlan:
    dest: Path
    items: list[CopyItem] = field(default_factory=list)
    total_bytes: int = 0  # bytes that will actually be written
    pictures: int = 0
    organise: bool = False  # raw/soc/ed folders in dest (see plan_copy)
    crop_items: list[CopyItem] = field(default_factory=list)  # one per cropped picture; size is an estimate
    story_items: list[CopyItem] = field(default_factory=list)  # one per story picture; size is an estimate


@dataclass
class CopyResult:
    copied: list[Path] = field(default_factory=list)  # source paths
    skipped: list[Path] = field(default_factory=list)  # source paths
    failed: list[tuple[Path, str]] = field(default_factory=list)
    bytes_copied: int = 0
    log_path: Path | None = None
    cancelled: bool = False
    rotated: list[Path] = field(default_factory=list)  # source paths whose copy got a new EXIF orientation
    rotation_kept: list[tuple[Path, str]] = field(default_factory=list)  # source path, why the copy kept its own
    cropped: list[Path] = field(default_factory=list)  # source primaries whose crop was written into ed
    stories: list[Path] = field(default_factory=list)  # source primaries whose story fit was written into ed


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


def _same_planned(src: Path, dst: Path, turns: int) -> bool:
    """dst already holds what a copy of src with `turns` would hold (src plus the orientation patch, if it applies)."""
    patch = None
    if turns:
        try:
            patch = rotation.plan_patch(src, turns)
        except Exception:  # noqa: BLE001 - a file that cannot be patched is copied (and reported) as it is
            patch = None
    return rotation.matches(src, dst, patch) if patch else _same_content(src, dst)


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


def _subdir(f: Path, has_raw: bool) -> str:
    """The folder an organised copy puts `f` in: raw for raw files, soc for other pictures, companions with the group."""
    ext = f.suffix.lower()
    if ext in RAW_EXTS:
        return RAW_DIR
    if ext in KNOWN_EXTS:
        return SOC_DIR
    return RAW_DIR if has_raw else SOC_DIR


CROP_RAW_BYTES = 12_000_000  # estimate of a full-frame JPEG made from a raw (its own size says nothing about a JPEG)


def estimate_crop_bytes(job: crop_mod.CropJob) -> int:
    """Rough size of a crop's JPEG: the cropped area's share of the pixel source's size (the sidecar or a non-raw
    primary, a JPEG-ish file), or of CROP_RAW_BYTES for a raw; at least 100 kB. Only used for the space check."""
    crop = crop_mod.normalise(job.crop) or (0.0, 0.0, 1.0, 1.0)
    area = (crop[2] - crop[0]) * (crop[3] - crop[1])
    primary = Path(job.primary)
    if job.sidecar is not None and preview.is_jpeg_sidecar(job.sidecar):
        whole = _size(Path(job.sidecar))
    elif primary.suffix.lower() in RAW_EXTS:
        whole = CROP_RAW_BYTES
    else:
        whole = _size(primary)
    return max(100_000, int(whole * area))


STORY_BYTES = 1_500_000  # fixed estimate of one 1080x1920 q92 JPEG (photographic content; not measured per picture)


def plan_copy(groups: Sequence[Sequence[Path]], dest: Path, organise: bool = False,
              turns: Mapping[Path, int] | None = None, crops: Sequence[crop_mod.CropJob] | None = None,
              stories: Sequence[story_mod.StoryJob] | None = None) -> CopyPlan:
    """Decide, per file, copy / skip / rename. Creates nothing.

    With ``organise`` each file targets ``dest/raw`` or ``dest/soc`` (companions follow the group's picture:
    raw if the group has a raw file, else soc); a RAF+JPG pair splits. Names are checked per target folder.
    ``turns`` maps a source file to the clockwise quarter turns to write into its copy as EXIF orientation; a copy
    already there counts as identical when it equals the source plus that patch.
    ``crops`` adds one ``plan.crop_items`` entry per job (see the module docstring); ``items`` is not affected.
    ``stories`` likewise adds ``plan.story_items`` (see the module docstring).
    """
    dest = Path(dest)
    plan = CopyPlan(dest=dest, organise=bool(organise))
    taken: set[tuple[str, str]] = set()  # (folder, name) claimed by this plan, case-folded (Windows/macOS volumes)
    for group in groups:
        files = [Path(f) for f in group]
        if not files:
            continue
        plan.pictures += 1
        has_raw = any(f.suffix.lower() in RAW_EXTS for f in files)
        subs = [_subdir(f, has_raw) if organise else "" for f in files]
        folders = [dest / sub if sub else dest for sub in subs]
        names = [f.name for f in files]
        # suffix 0: every file either free or an identical copy already there
        actions: list[str] | None = []
        for f, sub, folder in zip(files, subs, folders):
            target = folder / f.name
            if (sub, f.name.casefold()) in taken:
                actions = None
                break
            if not os.path.lexists(target):
                actions.append("copy")
            elif _same_planned(f, target, int((turns or {}).get(f, 0)) % 4):
                actions.append("skip")
            else:
                actions = None
                break
        n = 0
        if actions is None:
            n = 1
            while n < MAX_SUFFIX and any(
                (sub, _suffixed(nm, n).casefold()) in taken or os.path.lexists(folder / _suffixed(nm, n))
                for nm, sub, folder in zip(names, subs, folders)
            ):
                n += 1
            actions = ["rename"] * len(files)
        for f, act, sub, folder in zip(files, actions, subs, folders):
            name = _suffixed(f.name, n)
            taken.add((sub, name.casefold()))
            size = _size(f)
            plan.items.append(CopyItem(f, folder / name, size, act, int((turns or {}).get(f, 0)) % 4))
            if act != "skip":
                plan.total_bytes += size
    ed = dest / ED_DIR

    def ed_name(stem: str, tail: str) -> str:
        n = 0
        while n < MAX_SUFFIX and (
            (ED_DIR, _suffixed(f"{stem}{tail}", n).casefold()) in taken
            or os.path.lexists(ed / _suffixed(f"{stem}{tail}", n))
        ):
            n += 1
        name = _suffixed(f"{stem}{tail}", n)
        taken.add((ED_DIR, name.casefold()))
        return name

    for job in crops or ():
        name = ed_name(Path(job.primary).stem, f"{ratio_mod.export_suffix(job.ratio or ratio_mod.FREE, job.turns)}.jpg")
        est = estimate_crop_bytes(job)
        plan.crop_items.append(CopyItem(Path(job.primary), ed / name, est, "crop", crop_job=job))
        plan.total_bytes += est
    for sjob in stories or ():
        name = ed_name(Path(sjob.primary).stem, "_story.jpg")
        plan.story_items.append(CopyItem(Path(sjob.primary), ed / name, STORY_BYTES, "story", story_job=sjob))
        plan.total_bytes += STORY_BYTES
    return plan


def logs_dir() -> Path:
    """`<QStandardPaths.AppDataLocation>/logs` (never created here); $SPUZA_LOGS_DIR wins. Never inside a destination."""
    env = os.environ.get(ENV_LOGS)
    if env:
        return Path(env)
    try:
        from PySide6.QtCore import QStandardPaths

        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    except Exception:
        base = ""
    return (Path(base) if base else Path.home() / ".spuza") / "logs"


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
        st = tmp.stat()  # times only: the temp is ordinary, but never carry a mode over
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
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
    if plan.organise:  # all three always exist after an organised copy; ed stays empty for Lightroom exports
        for sub in (RAW_DIR, SOC_DIR, ED_DIR):
            try:
                (plan.dest / sub).mkdir(exist_ok=True)
            except OSError:
                pass  # a file in the way: its pictures fail one by one and are reported

    def report(src: Path) -> None:
        if progress:
            progress(done, total, src)

    def copy_one(item: CopyItem) -> tuple[Path, rotation.Outcome | None]:
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
            outcome = rotation.apply_rotation(item.src, tmp, item.turns) if item.turns else None
            # Times only, never the mode: a read-only card file would make the temp (and, hard-linked, the copy)
            # read-only too, and the temp could then not be removed. The copy stays an ordinary owner-writable file.
            st = item.src.stat()
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
            dst = item.dst
            while True:
                try:
                    _publish(tmp, dst)
                    break
                except FileExistsError:
                    dst = _free_name(item.dst)  # target appeared meanwhile: rename, never overwrite
            return dst, outcome
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
            final, outcome = copy_one(item)
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
        if outcome is not None and outcome.status == "written":
            res.rotated.append(item.src)
            notes.append(f"ROTATE {final}: orientation {outcome.before} -> {outcome.after} "
                         f"(checked: {', '.join(outcome.checked_with)})")
        elif outcome is not None and outcome.status == "kept":
            res.rotation_kept.append((item.src, outcome.reason))
            notes.append(f"KEPT   {final}: original orientation kept ({outcome.reason})")

    def ed_one(item: CopyItem, render: Callable[[], object], write: Callable[[object, Path], None]) -> tuple[Path, int]:
        """Render (seconds for a raw: this is the worker), write a temp, publish without replacing."""
        if cancel and cancel():
            raise _Cancelled
        made = render()
        if cancel and cancel():
            raise _Cancelled
        item.dst.parent.mkdir(parents=True, exist_ok=True)  # flat layout: ed is made on first use
        tmp = item.dst.with_name(f".{item.dst.name}.spuza-part")
        try:
            write(made, tmp)
            size = tmp.stat().st_size
            try:  # the result keeps the original's file times (not its mode: a read-only card file would stick)
                st = item.src.stat()
                os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
            except OSError:
                pass
            dst = item.dst
            while True:
                try:
                    _publish(tmp, dst)
                    break
                except FileExistsError:
                    dst = _free_name(item.dst)
            return dst, size
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def crop_one(item: CopyItem) -> tuple[Path, int]:
        job = item.crop_job
        if job is None:
            raise crop_mod.CropError("no crop to render")
        return ed_one(item, lambda: crop_mod.render(job.primary, job.sidecar, job.crop, job.turns),
                      lambda im, tmp: crop_mod.write_jpeg(im, tmp))

    def story_one(item: CopyItem) -> tuple[Path, int]:
        job = item.story_job
        if job is None:
            raise crop_mod.CropError("no story to render")
        return ed_one(item, lambda: story_mod.export(job), lambda made, tmp: story_mod.save(made[0], tmp, made[1]))

    def run_ed(items: list[CopyItem], one, word: str, tag: str, out: list[Path]) -> None:
        nonlocal done
        for item in () if res.cancelled else items:
            if cancel and cancel():
                res.cancelled = True
                break
            try:
                final, size = one(item)
            except _Cancelled:
                res.cancelled = True
                notes.append(f"CANCEL {word} of {item.src}")
                break
            except Exception as e:  # one bad crop or story must not stop the rest
                done += item.size
                report(item.src)
                res.failed.append((item.src, f"{word.capitalize()} failed: {type(e).__name__}: {e}"))
                notes.append(f"FAIL   {word} of {item.src}: {type(e).__name__}: {e}")
                continue
            done += item.size
            res.bytes_copied += size
            report(item.src)
            out.append(item.src)
            notes.append(f"{tag} {item.src} -> {final}")

    run_ed(plan.crop_items, crop_one, "crop", "CROP  ", res.cropped)
    run_ed(plan.story_items, story_one, "story", "STORY ", res.stories)

    if log_path is None:
        log_path = logs_dir() / f"spuza-copy-{datetime.now():%Y%m%d-%H%M%S}.log"
    lines = [f"Spuza copy to {plan.dest}", *notes]
    lines.append(
        f"Totals: {len(res.copied)} copied, {len(res.skipped)} skipped, {len(res.failed)} failed, "
        f"{human_size(res.bytes_copied)} written" + (", CANCELLED" if res.cancelled else "")
    )
    if res.rotated or res.rotation_kept:
        lines.append(f"Rotation: {len(res.rotated)} written, {len(res.rotation_kept)} kept their original orientation")
    if res.cropped:
        lines.append(f"cropped: {len(res.cropped)} into {ED_DIR}")
    if res.stories:
        lines.append(f"story: {len(res.stories)} into {ED_DIR}")
    try:
        res.log_path = _write_log(Path(log_path), "\n".join(lines) + "\n")
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
