"""The one table of every user action: it drives the menu bar and the shortcut overlay, so they cannot drift.

Keys are dispatched where they always were (``MainWindow.keyPressEvent`` and its QShortcuts); the menu's
QActions only *display* them (and run the same handler when clicked). This module imports Qt only inside native_keys(): docs/check_readme.py
parses it with ``ast`` (so keep ``ACTIONS`` a list of literal ``Action(...)`` calls), and tests read it plainly.

``handler`` names a MainWindow method that takes no arguments. ``flag`` is a ``features`` flag: the action is
hidden while that flag is off (advanced-only). ``state`` names the toolbar button whose checked / enabled state the
action mirrors ("" for none; "next:<name>" is one of the advanced row's buttons). Checkable actions with no button
(lights_out, dark_theme) are kept in step by MainWindow.refresh_actions from the window's own state.

The Pills actions (pill_1..pill_9, pill_clear, pills_manage) are static here, so the policy tests and check_readme see
every key; at run time MainWindow.refresh_actions relabels pill_N with its pill's name and colour dot, checks it when
the current picture carries the pill, and hides it while no pill owns that digit.
"""

from __future__ import annotations

from dataclasses import dataclass

MENUS = ("File", "Edit", "View", "Picture", "Pills", "Help")
GROUPS = ("Navigate", "Mark", "View", "Picture", "Pills", "Crop / Story", "Review", "Compare", "App")


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
    Action("back", "View", "Back", ("Left", "J"), "act_back", "Navigate", state="back"),
    Action("next", "View", "Next", ("Right", "K"), "act_next", "Navigate", state="next"),
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
    # layout and look: not mirrored by a toolbar button; their checked state is the window's (lights_out, theme)
    Action("lights_out", "View", "Lights out", ("Tab",), "act_lights_out", "View", checkable=True, needs_picture=False),
    Action("dark_theme", "View", "Dark theme", ("Ctrl+T",), "act_dark_theme", "View", checkable=True,
           needs_picture=False),  # a modifier key: a stray T while culling must not flip a saved preference
    Action("rotate_left", "Picture", "Rotate left", ("[",), "act_rotate_left", "Picture"),
    Action("rotate_right", "Picture", "Rotate right", ("]",), "act_rotate_right", "Picture"),
    Action("crop", "Picture", "Crop", ("X",), "act_crop", "Picture", needs_viewer=True),
    Action("story", "Picture", "Story fit", ("S",), "act_story", "Picture", needs_viewer=True),
    Action("remove", "Picture", "Remove crop / story", ("Backspace",), "act_remove", "Picture", needs_viewer=True),
    Action("pill_1", "Pills", "Pill 1", ("1",), "act_pill_1", "Pills", checkable=True),
    Action("pill_2", "Pills", "Pill 2", ("2",), "act_pill_2", "Pills", checkable=True),
    Action("pill_3", "Pills", "Pill 3", ("3",), "act_pill_3", "Pills", checkable=True),
    Action("pill_4", "Pills", "Pill 4", ("4",), "act_pill_4", "Pills", checkable=True),
    Action("pill_5", "Pills", "Pill 5", ("5",), "act_pill_5", "Pills", checkable=True),
    Action("pill_6", "Pills", "Pill 6", ("6",), "act_pill_6", "Pills", checkable=True),
    Action("pill_7", "Pills", "Pill 7", ("7",), "act_pill_7", "Pills", checkable=True),
    Action("pill_8", "Pills", "Pill 8", ("8",), "act_pill_8", "Pills", checkable=True),
    Action("pill_9", "Pills", "Pill 9", ("9",), "act_pill_9", "Pills", checkable=True),
    Action("pill_clear", "Pills", "Clear all pills", ("0",), "act_pill_clear", "Pills"),
    Action("pills_manage", "Pills", "Manage pills…", ("Ctrl+Shift+P",), "act_pills_manage", "Pills",
           needs_picture=False),
    Action("help", "Help", "Keyboard shortcuts", ("?",), "act_help", "App", needs_picture=False),
]

# Keys that only mean something inside a mode, so they are not menu actions: (group, keys, what it does)
CONTEXT_KEYS = [
    ("Pills", "1 - 9 / 0", "Toggle a pill, clear them (not in crop or story)"),
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


PILL_DIGITS = {f"pill_{n}": n for n in range(1, 10)}  # the actions whose label and visibility follow the PillBook


def by_id() -> dict[str, Action]:
    return {a.id: a for a in ACTIONS}


def native_keys(a: Action) -> str:
    """The keys as this platform writes them (Ctrl is the command key on macOS), joined with ' / '."""
    from PySide6.QtGui import QKeySequence

    return " / ".join(QKeySequence(k).toString(QKeySequence.SequenceFormat.NativeText) for k in a.keys)
