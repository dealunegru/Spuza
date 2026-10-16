"""The camera's AF point from the Fuji makernote (flag focus_point, advanced-only).

No Qt here. Fails soft: a missing, truncated or hostile makernote yields None, never an exception. Called from
`preview.load` on the prefetch worker only, and only while `features.enabled("focus_point")`.
FocusPixel (0x1023) is in the pixel frame of the RAF's embedded JPEG: confirmed by the lead's probe
(state/plans/spuza-features-r1/focus-probe.md in MrAgent) on the models in ALLOWLIST. Owned by task spz-nx-focus.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bodies whose frame the probe confirmed in AF (X-T4 and X-T5 Single Point; X100V tracking, X-T50 zone).
ALLOWLIST = frozenset({"X-T4", "X-T5", "X100V", "X-T50"})


@dataclass(frozen=True)
class FocusInfo:
    model: str | None  # "X-T5"
    mode: str | None  # FocusMode, e.g. "AF-S", "AF-C", "Manual"
    area: str | None  # AFMode / area, e.g. "Single Point", "Zone", "Wide/Tracking"
    point: tuple[int, int] | None  # FocusPixel (x, y) in the embedded JPEG's stored frame; None without one
    frame: tuple[int, int] | None  # that JPEG's stored (width, height)
    orientation: int = 1  # EXIF orientation of that JPEG; frac() maps the point through it

    def usable(self) -> bool:
        """A point we may show: present, inside its frame, an allowlisted model, and not manual focus."""
        raise NotImplementedError

    def frac(self) -> tuple[float, float] | None:
        """(fx, fy) of the point in the DISPLAYED (oriented) picture, each 0-1; None unless usable()."""
        raise NotImplementedError

    def describe(self) -> str:
        """The inspector line: "Focus: point (2207, 1472) · AF-S Single Point", "Focus: manual", or
        "Focus: no point"."""
        raise NotImplementedError


def read(path) -> FocusInfo | None:
    """The focus data of a RAF at `path`, or None for any other file or when it carries none. Never raises."""
    raise NotImplementedError
