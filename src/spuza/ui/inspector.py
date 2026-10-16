"""Right-hand inspector: the pair explained, where the preview came from, and whether it is marked."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from spuza.catalog import RAW_EXTS
from spuza.copy import human_size
from spuza.ui import theme


def explain(pic) -> tuple[str, bool]:
    """(headline, amber) for a picture."""
    ext = pic.primary.suffix.lower()
    if ext in RAW_EXTS:
        raw = ext[1:].upper()
        if pic.sidecar is not None:
            return f"{raw} + JPG: one picture, both files copy together", False
        return f"{raw} only: no JPG beside it", True
    return f"{pic.kind}: one file", False


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

    def set_picture(self, pic, info=None, marked=False, reason: str | None = None) -> None:
        self._args = (pic, info, marked, reason)
        if pic is None:
            self.lines, self.amber = [], False
            self.label.setText("")
            return
        head, amber = explain(pic)
        lines = [pic.stem, head]
        for f in pic.files:
            try:
                size = f"  {human_size(f.stat().st_size)}"
            except OSError:
                size = f"  {human_size(pic.size_bytes)}" if len(pic.files) == 1 else ""
            lines.append(f"{f.name}{size}")
        if len(pic.files) > 1:
            lines.append(f"Together  {human_size(pic.size_bytes)}")
        if reason:
            lines.append(f"Preview failed: {reason}")
        elif info is not None:
            lines.append(f"Preview: {info.source}")
            lines.append(f"Size: {info.width} x {info.height}")
            lines.append(f"Decode: {info.ms:.1f} ms")
        else:
            lines.append("Preview: loading...")
        lines.extend(exif_lines(info if not reason else None))
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
