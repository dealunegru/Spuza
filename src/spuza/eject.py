"""Eject a card after a copy. Never raises, never hangs: every step is a subprocess with a timeout.

``eject(mount)`` returns ``(ok, message)``; the message is a short reason on failure. ``run`` and
``platform`` are injectable so tests never touch a real drive. macOS: ``diskutil eject``, then
``diskutil unmount``. Windows: the Shell "Eject" verb, then up to ``WIN_CHECKS`` looks (about 5 s in all)
to see the drive root gone. Anywhere else: "Eject is not supported here".
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

WIN_CHECKS = 10  # looks at the drive root after the Eject verb
WIN_CHECK_GAP = 0.5  # seconds between looks: 10 x 0.5 = at most about 5 s


def _short(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _try(run, cmd: list[str], timeout: float) -> tuple[bool, str]:
    try:
        r = run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except OSError as e:
        return False, _short(str(e)) or "could not run"
    except Exception as e:  # noqa: BLE001 - this function never raises
        return False, _short(f"{type(e).__name__}: {e}")
    if getattr(r, "returncode", 1) == 0:
        return True, ""
    out = _short(f"{getattr(r, 'stderr', '') or ''} {getattr(r, 'stdout', '') or ''}")
    return False, out or f"exit {getattr(r, 'returncode', '?')}"


def _drive_root(mount: Path) -> str:
    drive = os.path.splitdrive(str(mount))[0]
    return (drive + "\\") if drive else str(mount)


def eject(mount: Path, timeout: float = 20.0, run=subprocess.run, platform: str = sys.platform,
          sleep=time.sleep, exists=os.path.exists) -> tuple[bool, str]:
    try:
        mount = Path(mount)
        if platform == "darwin":
            ok, why = _try(run, ["diskutil", "eject", str(mount)], timeout)
            if ok:
                return True, "ejected"
            ok2, why2 = _try(run, ["diskutil", "unmount", str(mount)], timeout)
            return (True, "unmounted") if ok2 else (False, why2 or why)
        if platform.startswith("win"):
            root = _drive_root(mount)
            quoted = root.replace("'", "''")  # inside a single-quoted PowerShell string
            script = ("(New-Object -ComObject Shell.Application).Namespace(17)"
                      f".ParseName('{quoted}').InvokeVerb('Eject')")
            ok, why = _try(run, ["powershell", "-NoProfile", "-Command", script], timeout)
            if not ok:
                return False, why
            for i in range(WIN_CHECKS):
                if not exists(root):
                    return True, "ejected"
                if i < WIN_CHECKS - 1:
                    sleep(WIN_CHECK_GAP)
            return False, "the drive is still mounted"
        return False, "Eject is not supported here"
    except Exception as e:  # noqa: BLE001
        return False, _short(f"{type(e).__name__}: {e}")
