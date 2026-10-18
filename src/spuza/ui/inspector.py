"""Right-hand inspector: the pair explained, where the preview came from, and whether it is marked."""

from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from spuza.catalog import RAW_EXTS
from spuza.copy import human_size
from spuza.ui import theme


def _companion_note(pic) -> str:
    comps = tuple(getattr(pic, "companions", ()) or ())
    if not comps:
        return ""
    exts = ", ".join(c.suffix[1:].upper() or c.name for c in comps)
    n = len(comps)
    if n == 1:
        return f"1 companion file ({exts}) copies with it"
    return f"{n} companion files ({exts}) copy with it"


def explain(pic) -> tuple[str, bool]:
    """(headline, amber) for a picture: what files make it up, from its kind and companions."""
    ext = pic.primary.suffix.lower()
    note = _companion_note(pic)
    if ext in RAW_EXTS:
        kind = pic.kind.replace("+", " + ")
        if "+" in pic.kind:
            head = f"{kind}: one picture, both files copy together"
            if pic.kind.count("+") > 1:
                head = f"{kind}: one picture, all files copy together"
            return (f"{head} + {note}" if note else head), False
        raw = ext[1:].upper()
        head = f"{raw} only: no JPG beside it"
        return (f"{head} - {note}" if note else head), True
    head = f"{pic.kind}: one file"
    return (f"{head} + {note}" if note else head), False


def exif_lines(info) -> list[str]:
    """Aperture / shutter / ISO rows always; focal and model only when the file has them. "--" = missing."""
    ex = getattr(info, "exif", None)
    rows = [("Aperture", getattr(ex, "aperture", None)), ("Shutter", getattr(ex, "shutter", None)),
            ("ISO", (getattr(ex, "iso", None) or "").removeprefix("ISO ") or None)]  # value is "ISO 200"
    out = [f"{k}: {v or '--'}" for k, v in rows]
    for k, v in (("Focal length", getattr(ex, "focal", None)), ("Camera", getattr(ex, "model", None))):
        if v:
            out.append(f"{k}: {v}")
    return out


def taken_line(pic, info=None) -> str:
    """One line: "Taken: 2026-10-18  21:43:05" (camera clock as written), or the file's local mtime
    with " (file date)". Scan time first (no file read here), then the preview's EXIF, then mtime."""
    for iso in (getattr(pic, "taken", ""), getattr(getattr(info, "exif", None), "taken", None)):
        try:  # an unset camera clock ("0000-00-00...") or any odd value is absent: try the next source
            if isinstance(iso, str) and len(iso) == 19:
                datetime.fromisoformat(iso)
                return f"Taken: {iso[:10]}  {iso[11:]}"
        except ValueError:
            pass
    try:
        mt = float(getattr(pic, "mtime", 0) or 0)
        if mt > 0:
            return "Taken: " + datetime.fromtimestamp(mt).strftime("%Y-%m-%d  %H:%M:%S") + " (file date)"
    except (OverflowError, OSError, ValueError):
        pass
    return ""


def edit_line(crop=None, story=None) -> str:
    """One row for a picture's crop ("Crop: 62% x 48% of the picture") or story fit ("Story fit: 9:16, zoom 1.4x");
    "" when it has neither. Pure arithmetic on two small values: no file is read."""
    try:
        if crop is not None:
            left, top, right, bottom = crop
            return f"Crop: {round((right - left) * 100)}% x {round((bottom - top) * 100)}% of the picture"
        if story is not None:
            return f"Story fit: 9:16, zoom {story.zoom:.1f}x"
    except (TypeError, ValueError, AttributeError):
        pass
    return ""


class Inspector(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(230)
        self.setMaximumWidth(300)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(self.label, 1)
        self.lines: list[str] = []
        self.amber = False
        self._args = None

    def set_picture(self, pic, info=None, marked=False, reason: str | None = None, edit: str = "") -> None:
        self._args = (pic, info, marked, reason)
        if pic is None:
            self.lines, self.amber = [], False
            self.label.setText("")
            return
        head, amber = explain(pic)
        lines = [pic.stem, head]
        comps = set(getattr(pic, "companions", ()) or ())
        for f in pic.files:
            try:
                size = f"  {human_size(f.stat().st_size)}"
            except OSError:
                size = f"  {human_size(pic.size_bytes)}" if len(pic.files) == 1 else ""
            lines.append(f"{f.name}{size}{'  (companion)' if f in comps else ''}")
        if len(pic.files) > 1:
            lines.append(f"Together  {human_size(pic.size_bytes)}")
        if reason:
            lines.append(f"Preview failed: {reason}")
        elif info is not None:
            ps = getattr(info, "picture_size", None)
            pw, ph = ps if ps else (info.width, info.height)  # unset (injected loader): the decoded size
            late = ps is None and hasattr(info, "picture_source") and getattr(info, "picture_source") is None
            lines.append(f"Size: {pw} x {ph}" + (" (preview; picture size still reading)" if late else ""))
            lines.append(f"Preview: {info.source} {info.width} x {info.height}")
            if str(getattr(info, "picture_source", None) or "").endswith("sidecar unreadable"):
                lines.append("JPG beside it unreadable: zoom uses the raw")
            lines.append(f"Decode: {info.ms:.1f} ms")
        else:
            lines.append("Preview: loading...")
        lines.extend(exif_lines(info if not reason else None))
        taken = taken_line(pic, info if not reason else None)
        if taken:
            lines.append(taken)
        focus = getattr(info, "focus", None) if not reason else None
        if focus is not None:
            lines.append(focus.describe())
        if edit:  # friend round 5: what a copy will do to this picture
            lines.append(edit)
        lines.append("Marked" if marked else "Not marked")
        self.lines, self.amber = lines, amber
        self._render()

    def retheme(self) -> None:
        if self._args is not None:
            self._render()

    def _render(self) -> None:
        c = theme.current()
        pic, info, marked, reason = self._args
        head, amber = explain(pic)
        out = [f"<div style='font-size:15px;font-weight:600'>{html.escape(pic.stem)}</div>",
               f"<p style='color:{c.amber if amber else c.text}'><b>{html.escape(head)}</b></p>"]
        body = self.lines[2:]
        for t in body:
            col = c.green if t == "Marked" else (c.amber if reason and t.startswith("Preview failed") else c.dim)
            out.append(f"<div style='color:{col};margin-bottom:3px'>{html.escape(t)}</div>")
        self.label.setText("".join(out))

    def plain(self) -> str:
        return "\n".join(self.lines)
