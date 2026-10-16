"""The `spuza` command: open a card (or any folder), plus --demo / --bench for timing it."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from spuza import __version__, features
from spuza.ui.settings import AHEAD_RANGE, BEHIND_RANGE, DEFAULT_THREADS, read_threads, read_window

_T0 = time.perf_counter()  # fallback "process start" when the OS will not tell us
BENCH_WAIT_S = 30.0  # longest any single wait in --bench may take


def _int_in(lo: int, hi: int):
    def conv(text: str) -> int:
        try:
            n = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
        if not lo <= n <= hi:
            raise argparse.ArgumentTypeError(f"{n} is outside {lo}..{hi}")
        return n

    return conv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="spuza", description="Fast photo culling for camera cards.")
    p.add_argument("path", nargs="?", help="folder or card to open (default: the first camera card found)")
    p.add_argument("--demo", metavar="PATH", help="open this folder and print timings to stderr")
    p.add_argument("--bench", action="store_true",
                   help="with --demo: run a scripted session (step all, Z on raws, step back) and exit")
    p.add_argument("--ahead", type=_int_in(*AHEAD_RANGE), metavar="N",
                   help=f"pictures to decode ahead, {AHEAD_RANGE[0]}..{AHEAD_RANGE[1]}"
                        " (this run only; the saved preference is untouched)")
    p.add_argument("--behind", type=_int_in(*BEHIND_RANGE), metavar="N",
                   help=f"pictures to keep behind, {BEHIND_RANGE[0]}..{BEHIND_RANGE[1]}"
                        " (this run only; the saved preference is untouched)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--advanced", dest="mode", action="store_const", const="advanced",
                   help="run in advanced mode (this run only; the saved preference is untouched)")
    g.add_argument("--classic", dest="mode", action="store_const", const="classic",
                   help="run in classic mode (this run only; the saved preference is untouched)")
    p.add_argument("--version", action="version", version=f"spuza {__version__}")
    return p


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = build_parser()
    a = p.parse_args(argv)
    if a.bench and not a.demo:
        p.error("--bench needs --demo PATH")
    return a


def window_from(args, prefs) -> tuple[int, int]:
    """(ahead, behind): each CLI flag overrides the saved preference. Reads `prefs`, never writes it."""
    ahead, behind = read_window(prefs)
    return (ahead if args.ahead is None else args.ahead,
            behind if args.behind is None else args.behind)


def threads_from(prefs) -> int:
    """Decode threads: the saved count in advanced mode (decode_threads flag), else the classic 3."""
    if features.enabled("decode_threads"):
        return read_threads(prefs)
    return DEFAULT_THREADS


def choose_root(cards: list[Path]) -> Path | None:
    """One card: its DCIM folder. Several cards: the first card's root (every folder on it)."""
    if not cards:
        return None
    first = Path(cards[0])
    if len(cards) == 1 and (first / "DCIM").is_dir():
        return first / "DCIM"
    return first


def _say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _process_age_ms() -> tuple[float, str]:
    """Milliseconds since this process started (Windows), else since this module was imported."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            k32.GetSystemTimeAsFileTime.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
            c, e, k, u, now = (wintypes.FILETIME() for _ in range(5))
            if k32.GetProcessTimes(k32.GetCurrentProcess(), c, e, k, u):
                k32.GetSystemTimeAsFileTime(now)

                def val(f):
                    return (f.dwHighDateTime << 32) | f.dwLowDateTime
                return (val(now) - val(c)) / 10000.0, "process start"
        except Exception:  # noqa: BLE001 - measurement only; fall back below
            pass
    return (time.perf_counter() - _T0) * 1000, "module import"


def _app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    app.setApplicationName("Spuza")
    from spuza.ui import gcguard

    gcguard.install(app)
    return app


def _pick_root(args) -> Path | None:
    from spuza.catalog import find_cards

    if args.demo or args.path:
        return Path(args.demo or args.path)
    root = choose_root(find_cards())
    if root is not None:
        return root
    from PySide6.QtWidgets import QFileDialog

    chosen = QFileDialog.getExistingDirectory(None, "Choose a card or a folder of pictures")
    return Path(chosen) if chosen else None


def _wait(app, cond, timeout: float) -> bool:
    """Spin the event loop until `cond()` or `timeout` seconds. Bounded; returns whether cond held."""
    from PySide6.QtCore import QEventLoop

    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            return False
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.002)
    return True


def run_bench(app, w) -> int:
    """Step through every picture, Z on raws, step back once; print per-picture lines and totals."""
    from spuza.catalog import RAW_EXTS

    v, cat = w.viewer, w.catalog
    n = len(cat)
    if n == 0:
        _say("bench: no pictures")
        return 1
    steps: list[tuple[int, float]] = []
    fulls: dict[int, float] = {}
    w.step_timed.connect(lambda i, ms: steps.append((i, ms)))
    v.full_done.connect(lambda i, ms: fulls.__setitem__(i, ms))
    ok = True
    rows = []

    def resolved(i):
        return v.index == i and (v.has_image or bool(v.error_text))

    for i in range(n):
        if i:
            w.step(1)
        got = _wait(app, lambda i=i: resolved(i), BENCH_WAIT_S)
        ok &= got
        pic = cat.pictures[i]
        failed = bool(v.error_text) and v.index == i
        step_ms = next((ms for j, ms in reversed(steps) if j == i), None)
        note = "" if got else " TIMEOUT"
        if failed:
            note += f" FAILED ({v.error_text})"
        elif pic.primary.suffix.lower() in RAW_EXTS and v.has_image:
            v.toggle_zoom()
            done = _wait(app, lambda i=i: v.full_state(i) in ("done", "skipped", "error"), BENCH_WAIT_S)
            ok &= done
            st = v.full_state(i) if done else "timeout"
            note += f" Z={st}" + (f" {fulls[i]:.0f} ms" if i in fulls else "")
            v.toggle_zoom()
        rows.append((i, pic, step_ms, failed))
        first = f"{step_ms:.1f} ms" if step_ms is not None else "n/a"
        _say(f"bench picture {i + 1}/{n} {pic.primary.name} [{pic.kind}] step {first}{note}")
    w.step(-1)
    back_ok = _wait(app, lambda: resolved(cat.index), BENCH_WAIT_S)
    ok &= back_ok
    back_ms = next((ms for j, ms in reversed(steps) if j == cat.index), None)
    timed = [ms for _i, _p, ms, _f in rows if ms is not None]
    mean = sum(timed) / len(timed) if timed else 0.0
    _say(f"bench back once: {back_ms:.1f} ms" if back_ms is not None else "bench back once: n/a")
    _say(f"bench total: {n} pictures, {sum(1 for r in rows if r[3])} failed, step mean {mean:.1f} ms"
         f" max {max(timed, default=0.0):.1f} ms, {len(fulls)} full decodes"
         f" mean {sum(fulls.values()) / len(fulls) if fulls else 0.0:.0f} ms, {'ok' if ok else 'TIMEOUT'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.demo or args.path) if (args.demo or args.path) else None
    if root is not None and not root.is_dir():
        _say(f"spuza: not a folder: {root}")
        return 1
    app = _app()
    from spuza.catalog import Catalog, Prefetcher, scan
    from spuza.ui import theme
    from spuza.ui.window import MainWindow

    if root is None:
        root = _pick_root(args)
        if root is None:
            _say("spuza: no card found and no folder chosen")
            return 1
    catalog = Catalog(scan(root))
    from spuza.ui.settings import open_settings

    prefs = open_settings()
    features.set_mode(features.resolve_mode(args.mode, prefs))  # before any window exists
    ahead, behind = window_from(args, prefs)
    threads = threads_from(prefs)
    # the default is left implicit so classic constructs the Prefetcher exactly as before
    extra = {} if threads == DEFAULT_THREADS else {"threads": threads}
    prefetcher = Prefetcher(catalog, ahead=ahead, behind=behind, **extra)
    window = MainWindow(catalog, prefetcher, root)
    if args.bench:
        theme.apply(app, theme.is_dark(app))
    else:
        window.follow_system_theme(app)

    seen_first: list[int] = []
    if args.demo:

        def first(i):
            if not seen_first:
                seen_first.append(i)
                ms, src = _process_age_ms()
                _say(f"first image painted: {ms:.0f} ms since {src}")

        window.viewer.painted.connect(first)
        window.step_timed.connect(lambda i, ms: _say(f"step to {i + 1}: {ms:.1f} ms"))
        window.viewer.full_done.connect(lambda i, ms: _say(f"Z full decode {i + 1}: {ms:.0f} ms"))
    window.show()
    if args.bench:
        if not _wait(app, lambda: bool(seen_first or window.viewer.error_text), BENCH_WAIT_S):
            _say("bench: first image TIMEOUT")
            window.close()
            return 1
        code = run_bench(app, window)
        window.close()
        return code
    code = app.exec()
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
