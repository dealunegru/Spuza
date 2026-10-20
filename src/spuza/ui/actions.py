"""The one table of every user action: it drives the menu bar and the shortcut overlay, so they cannot drift.

Keys are dispatched where they always were (``MainWindow.keyPressEvent`` and its QShortcuts); the menu's
QActions only *display* them (and run the same handler when clicked). This module imports Qt only inside native_keys(): docs/check_readme.py
parses it with ``ast`` (so keep ``ACTIONS`` a list of literal ``Action(...)`` calls), and tests read it plainly.

``handler`` names a MainWindow method that takes no arguments. ``flag`` is a ``features`` flag: the action is
hidden while that flag is off (advanced-only). ``state`` names the toolbar button whose checked / enabled state the
action mirrors ("" for none; "next:<name>" is one of the advanced row's buttons).
"""

from __future__ import annotations

from dataclasses import dataclass

MENUS = ("File", "Edit", "View", "Picture", "Help")
GROUPS = ("Navigate", "Mark", "View", "Picture", "Crop / Story", "Review", "Compare", "App")


@dataclass(frozen=True)
class Action:
    id: str
    menu: str
    label: str
    keys: tuple[str, ...]
    handler: str
    group: str
    flag: str = ""  # a features flag, or "" when always shown
    checkable: bool = False
    state: str = ""  # the toolbar button this action mirrors
    needs_picture: bool = True  # disabled while the folder has no pictures
    needs_viewer: bool = False  # disabled while the review grid is shown


ACTIONS = [
    Action("copy", "File", "Copy…", ("C",), "act_copy", "App", state="copy"),
    Action("prefs", "File", "Preferences…", ("Ctrl+,",), "act_prefs", "App", needs_picture=False),
    Action("quit", "File", "Quit", ("Ctrl+Q",), "act_quit", "App", needs_picture=False),
    Action("mark", "Edit", "Mark", ("Space", "M"), "act_mark", "Mark", checkable=True, state="mark"),
    Action("advance", "Edit", "Move on mark", ("A",), "act_advance", "Mark", checkable=True, state="advance"),
    Action("mark_all", "Edit", "Mark all", ("Ctrl+A",), "act_mark_all", "Mark"),
    Action("unmark_all", "Edit", "Unmark all", ("Ctrl+Shift+A",), "act_unmark_all", "Mark"),
    Action("undo_mark_all", "Edit", "Undo mark all", ("Ctrl+Z",), "act_undo_mark_all", "Mark"),
    Action("start_fresh", "Edit", "Start fresh", ("U",), "act_start_fresh", "Mark", flag="resume_session",
           needs_viewer=True),
    Action("back", "View", "Back", ("Left", "K"), "act_back", "Navigate", state="back"),
    Action("next", "View", "Next", ("Right", "J"), "act_next", "Navigate", state="next"),
    Action("zoom", "View", "Zoom 100%", ("Z",), "act_zoom", "View", checkable=True, state="zoom"),
    Action("zoom_in", "View", "Zoom in", ("+", "="), "act_zoom_in", "View"),
    Action("zoom_out", "View", "Zoom out", ("-",), "act_zoom_out", "View"),
    Action("fullscreen", "View", "Full screen", ("F",), "act_fullscreen", "View", checkable=True,
           state="fullscreen"),
    Action("review", "View", "Review", ("R",), "act_review", "Review", checkable=True, state="review"),
    Action("compare", "View", "Compare", ("N",), "act_compare", "Compare", flag="compare_view", checkable=True,
           state="next:compare"),
    Action("peaking", "View", "Peaking", ("P",), "act_peaking", "View", flag="focus_peaking", checkable=True,
           state="next:peaking"),
    Action("afpoint", "View", "AF point", ("O",), "act_afpoint", "View", flag="focus_point", checkable=True,
           state="next:afpoint"),
    Action("rotate_left", "Picture", "Rotate left", ("[",), "act_rotate_left", "Picture"),
    Action("rotate_right", "Picture", "Rotate right", ("]",), "act_rotate_right", "Picture"),
    Action("crop", "Picture", "Crop", ("X",), "act_crop", "Picture", needs_viewer=True),
    Action("story", "Picture", "Story fit", ("S",), "act_story", "Picture", needs_viewer=True),
    Action("remove", "Picture", "Remove crop / story", ("Backspace",), "act_remove", "Picture", needs_viewer=True),
    Action("help", "Help", "Keyboard shortcuts", ("?",), "act_help", "App", needs_picture=False),
]

# Keys that only mean something inside a mode, so they are not menu actions: (group, keys, what it does)
CONTEXT_KEYS = [
    ("Crop / Story", "1 - 8", "Crop: pick the ratio"),
    ("Crop / Story", "V", "Crop: turn the ratio"),
    ("Crop / Story", "0", "Crop: largest rectangle; story: whole picture"),
    ("Crop / Story", "Enter", "Make the crop or story picture"),
    ("Crop / Story", "Esc", "Cancel the crop or story fit"),
    ("Review", "← → ↑ ↓", "Move through the marked pictures"),
    ("Review", "Enter", "Open the selected picture"),
    ("Review", "Esc", "Leave the review"),
    ("Compare", "Esc", "Leave compare"),
]


def by_id() -> dict[str, Action]:
    return {a.id: a for a in ACTIONS}


def native_keys(a: Action) -> str:
    """The keys as this platform writes them (Ctrl is the command key on macOS), joined with ' / '."""
    from PySide6.QtGui import QKeySequence

    return " / ".join(QKeySequence(k).toString(QKeySequence.SequenceFormat.NativeText) for k in a.keys)
