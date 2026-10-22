"""Right-hand inspector: the pair explained, where the preview came from, and whether it is marked."""

from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from spuza import pills as pills_mod
from spuza.ui import star_bar as stars_mod
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


def edit_lines(crop=None, story=None, ratio=None, native=None, turns: int = 0) -> list[str]:
    """The rows for a picture's edits, crop first: "Crop: 5:4, 5000 x 4000 px" ("free" / "original" by name), then
    "Story fit: 9:16, zoom 1.4x". `native` is the picture's un-turned (w, h); pixels are given as seen on screen
    (swapped for an odd `turns`). Without a usable native size the crop reads as a share of the picture. [] when
    the picture has neither. Pure arithmetic on small values: no file is read."""
    from spuza import ratio as ratio_mod

    out: list[str] = []
    try:
        if crop is not None:
            left, top, right, bottom = crop
            r = ratio if isinstance(ratio, ratio_mod.Ratio) else ratio_mod.FREE
            name = ratio_mod.label(r, turns).lower() if r.entry in (1, 2) else ratio_mod.label(r, turns)
            try:
                pw, ph = ratio_mod.pixel_size(crop, native[0], native[1])
                if int(turns) % 2 == 1:
                    pw, ph = ph, pw
                out.append(f"Crop: {name}, {pw} x {ph} px")
            except (TypeError, ValueError, IndexError):
                out.append(f"Crop: {name}, {round((right - left) * 100)}% x {round((bottom - top) * 100)}% of the picture")
    except (TypeError, ValueError, AttributeError):
        pass
    try:
        if story is not None:
            out.append(f"Story fit: 9:16, zoom {story.zoom:.1f}x")
    except (TypeError, ValueError, AttributeError):
        pass
    return out


def derived_lines(d, source_name: str, native=None, turns: int = 0) -> tuple[str, list[str]]:
    """(headline, rows) for a derived picture. A crop: "Crop of <file>", its ratio ("Free" for none) and its size in
    pixels, "<w> x <h> px", from the source's un-turned native (w, h) and given as seen on screen (swapped for an odd
    `turns`). A story: "Story fit of <file>", "9:16", "1080 x 1920 px". Pure arithmetic: no file is read."""
    from spuza import derived as dv
    from spuza import ratio as ratio_mod
    from spuza import story as story_mod

    if not isinstance(d, dv.Derived):
        return f"Derived picture of {source_name}", []
    if d.kind == dv.STORY:
        return f"Story fit of {source_name}", ["9:16", f"{story_mod.FRAME_W} x {story_mod.FRAME_H} px"]
    try:
        rows = ["Free" if d.ratio is None else ratio_mod.label(d.ratio, turns)]
    except (TypeError, ValueError, AttributeError):
        rows = ["Free"]
    try:
        pw, ph = ratio_mod.pixel_size(d.crop, native[0], native[1])
        if int(turns) % 2 == 1:
            pw, ph = ph, pw
        rows.append(f"{pw} x {ph} px")
    except (TypeError, ValueError, IndexError, OverflowError):
        try:
            left, top, right, bottom = d.crop
            rows.append(f"{round((right - left) * 100)}% x {round((bottom - top) * 100)}% of the picture")
        except (TypeError, ValueError, OverflowError):
            pass
    return f"Crop of {source_name}", rows


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
        self.pills: list[tuple[str, str]] = []  # (name, swatch name) of the picture's pills, shown as "Pills: • Hero"
        self.stars = 0  # the picture's rating, 0..5 (0: not rated)
        self._head: str | None = None  # a derived picture's own headline (else explain() names the files)

    def set_picture(self, pic, info=None, marked=False, reason: str | None = None, edit=()) -> None:
        self._args = (pic, info, marked, reason)
        self._head = None
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
        if edit:  # friend round 5: what a copy will do to this picture (round 6: a list; a str is one row)
            lines.extend([edit] if isinstance(edit, str) else edit)
        lines.append("Marked" if marked else "Not marked")
        self.lines, self.amber = lines, amber
        self._render()

    def set_derived(self, pic, head: str, rows, marked=False) -> None:
        """A derived picture (friend round 7): its headline, its rows, and whether it is marked."""
        self._args = (pic, None, marked, None)
        self._head = head
        self.lines, self.amber = [pic.stem, head, *rows, "Marked" if marked else "Not marked"], False
        self._render()

    def set_pills(self, pills) -> None:
        """The pills of the picture on show; the next set_picture / set_derived (or this call) draws them."""
        self.pills = [(str(n), str(c)) for n, c in pills]
        if self._args is not None and self._args[0] is not None:
            self._render()

    def set_stars(self, n: int) -> None:
        """The star rating (0..5) of the picture on show; 0 reads "not rated"."""
        self.stars = stars_mod.clamp_stars(n)
        if self._args is not None and self._args[0] is not None:
            self._render()

    def stars_line(self) -> str:
        return "Stars: " + (f"{stars_mod.stars_text(self.stars)} {self.stars} of 5" if self.stars else "not rated")

    def pills_line(self) -> str:
        return "Pills: " + " ".join(f"• {n}" for n, _c in self.pills) if self.pills else ""

    def retheme(self) -> None:
        if self._args is not None and self._args[0] is not None:  # no picture (an empty catalog): nothing to repaint, bug-437
            self._render()

    def _render(self) -> None:
        c = theme.current()
        pic, info, marked, reason = self._args
        head, amber = explain(pic)
        if self._head is not None:
            head, amber = self._head, False
        out = [f"<div style='font-size:15px;font-weight:600'>{html.escape(pic.stem)}</div>",
               f"<p style='color:{c.warning if amber else c.text}'><b>{html.escape(head)}</b></p>"]
        body = self.lines[2:]
        for t in body:
            col = c.marked if t == "Marked" else (c.warning if reason and t.startswith("Preview failed") else c.dim)
            out.append(f"<div style='color:{col};margin-bottom:3px'>{html.escape(t)}</div>")
        if self.pills:  # after "Marked": the bullets take the pills' own colours
            bullets = "".join(
                f" <span style='color:{pills_mod.swatch(col).dark if c.dark else pills_mod.swatch(col).light}'>"
                f"{'○' if pills_mod.swatch(col).finder == 0 else '●'}</span> {html.escape(n)}" for n, col in self.pills)
            out.append(f"<div style='color:{c.dim};margin-bottom:3px'>Pills:{bullets}</div>")
        out.append(f"<div style='color:{c.dim};margin-bottom:3px'>{html.escape(self.stars_line())}</div>")
        self.label.setText("".join(out))

    def plain(self) -> str:
        return "\n".join([*self.lines, *([self.pills_line()] if self.pills else [])])
