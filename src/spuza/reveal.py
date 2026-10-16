"""Show a picture's file in the platform's file manager (right-click menu: viewer, filmstrip, review grid).

Fails soft: a missing file manager is logged and reported through the return value, never raised into
the UI. The platform and the launcher are parameters so tests launch nothing.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def reveal_label(platform: str = sys.platform) -> str:
    if platform == "darwin":
        return "Reveal in Finder"
    if platform == "win32":
        return "Show in Explorer"
    return "Show in file manager"


def reveal_target(pic) -> Path:
    """The file to point at: the raw for a RAW+JPG pair (`primary` is the raw, the JPEG is the sidecar)."""
    return Path(pic.primary)


def _argv(path: Path, platform: str) -> list[str]:
    if platform == "darwin":
        return ["open", "-R", str(path)]
    if platform == "win32":
        # Explorer wants backslashes; "/select," and the path as separate arguments so a path with
        # spaces is quoted on its own: explorer /select, "C:\a b\c.RAF"
        return ["explorer", "/select,", str(path).replace("/", "\\")]
    return ["xdg-open", str(path.parent)]


def reveal(path, platform: str = sys.platform, run=subprocess.Popen) -> OSError | None:
    """Launch the file manager on `path`. Returns the OSError if it could not start, else None.

    The exit code is not looked at (and the process is not waited for): Explorer exits 1 on success.
    """
    try:
        run(_argv(Path(path), platform))
    except OSError as exc:
        log.warning("could not reveal %s: %s", path, exc)
        return exc
    return None


def reveal_menu(parent, pic):
    """A one-action QMenu ("Reveal in Finder" and kin) for `pic`; not shown, so tests can trigger it."""
    from PySide6.QtWidgets import QMenu

    menu = QMenu(parent)
    action = menu.addAction(reveal_label())
    action.triggered.connect(lambda _checked=False, p=pic: reveal_now(p))
    return menu


def reveal_now(pic) -> None:
    reveal(reveal_target(pic))  # global lookup at call time: a patched reveal() is honoured
