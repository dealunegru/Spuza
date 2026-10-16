"""The thin ring at the camera's AF point on the big picture (flag focus_point, advanced-only).

Contract for the window (wired by spz-nx-wire): build one `FocusMarker(viewer)`; `set_on(...)` when O toggles;
`set_point(frac)` after each picture is shown, with `PreviewInfo.focus.frac()` or None. The ring is a child of
`viewer.image_item()`, so it follows the fitted view, the 100% view and the full-resolution swap. Owned by task
spz-nx-focus.
"""

from __future__ import annotations


class FocusMarker:
    def __init__(self, viewer):
        raise NotImplementedError

    def is_on(self) -> bool:
        raise NotImplementedError

    def set_on(self, on: bool) -> None:
        raise NotImplementedError

    def set_point(self, frac: tuple[float, float] | None) -> None:
        """Where the ring goes, as a fraction of the displayed picture; None hides it."""
        raise NotImplementedError

    def visible(self) -> bool:
        """The ring is on screen (on, and a point is set)."""
        raise NotImplementedError
