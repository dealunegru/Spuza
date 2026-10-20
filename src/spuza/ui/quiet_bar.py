"""Direction A, the Quiet top bar: slim, icon-only buttons, a filled Copy, an overflow menu.

Wired into MainWindow (``window.quiet_bar``). The window supplies its existing buttons
(``add_button``) so their objects, signals and shortcuts stay as they are.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from . import icons, theme

# tokens.json dark values, used when theme.Colors predates the new fields.
_DARK = {
    "window": "#232323", "text": "#e6e6e6", "text_dim": "#9e9e9e", "border": "#383838",
    "hover": "#3a3a3a", "pressed": "#444444", "focus": "#5a9bff", "on_focus": "#ffffff",
    "text_disabled": "#5e5e5e",
}
BAR_HEIGHT = 38
ICON_SIZE = 18


def _col(c, name: str) -> str:
    fallback = _DARK.get(name, "#808080")
    if name == "text_dim":  # today's Colors calls it ``dim``
        return getattr(c, "text_dim", getattr(c, "dim", fallback))
    return getattr(c, name, fallback)


def tooltip_text(name: str, key: str = "") -> str:
    """'Name  Key' on one line (spaces non-breaking so Qt never wraps it)."""
    return f"{name}  {key}".strip().replace(" ", " ")


class QuietBar(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("quietBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(BAR_HEIGHT)
        self._icon_names: dict[QToolButton, str] = {}
        self._copy: QToolButton | None = None
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(10, 3, 10, 3)
        self._lay.setSpacing(2)
        self._title = QLabel("")
        self._title.setObjectName("quietTitle")
        self._title.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._lay.addWidget(self._title)
        self._lay.addStretch(1)
        self._more = QToolButton()
        self._more.setObjectName("quietMore")
        self._more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self._more)
        self._more.setMenu(self._menu)
        self._icon_names[self._more] = "ellipsis"
        self.set_tooltip(self._more, "More")
        self._style_icon_button(self._more)
        self._lay.addWidget(self._more)
        self.retheme()

    # ---- public API -------------------------------------------------------------------------
    def set_title(self, folder: str, count: int | None = None) -> None:
        self._title.setText(folder if count is None else f"{folder}  ·  {count}")

    def title(self) -> str:
        return self._title.text()

    def add_button(self, btn: QToolButton, icon_name: str) -> QToolButton:
        """Put an existing button in the bar, icon-only. The same object is kept."""
        self._icon_names[btn] = icon_name
        self._style_icon_button(btn)
        btn.setIcon(icons.themed_icon(icon_name, theme.current(), ICON_SIZE))
        self._lay.insertWidget(self._lay.indexOf(self._more), btn)
        return btn

    def set_copy_button(self, btn: QToolButton) -> QToolButton:
        """The filled primary Copy button, right of the overflow."""
        if self._copy is not None and self._copy is not btn:
            self._lay.removeWidget(self._copy)
            self._icon_names.pop(self._copy, None)
        self._copy = btn
        self._icon_names[btn] = "copy"
        btn.setProperty("primary", True)  # the stylesheet's hook; the button keeps its own object name
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._lay.addWidget(btn)
        self.retheme()
        return btn

    def overflow_menu(self) -> QMenu:
        return self._menu

    def set_tooltip(self, btn: QWidget, name: str, key: str = "") -> None:
        btn.setToolTip(tooltip_text(name, key))

    def buttons(self) -> list[QToolButton]:
        return list(self._icon_names)

    def retheme(self) -> None:
        c = theme.current()
        for btn, nm in self._icon_names.items():
            if btn is self._copy:
                btn.setIcon(icons.icon(nm, _col(c, "on_focus"), ICON_SIZE))
            else:
                btn.setIcon(icons.themed_icon(nm, c, ICON_SIZE))
        bg, text, dim = _col(c, "window"), _col(c, "text"), _col(c, "text_dim")
        hover, pressed, border = _col(c, "hover"), _col(c, "pressed"), _col(c, "border")
        focus, on_focus = _col(c, "focus"), _col(c, "on_focus")
        self.setStyleSheet(f"""
            #quietBar {{ background: {bg}; border-bottom: 1px solid {border}; }}
            #quietTitle {{ color: {dim}; padding-left: 4px; }}
            #quietBar QToolButton {{ background: transparent; color: {text}; border: none;
                border-radius: 6px; padding: 4px; }}
            #quietBar QToolButton:hover {{ background: {hover}; }}
            #quietBar QToolButton:pressed {{ background: {pressed}; }}
            #quietBar QToolButton:checked {{ background: {hover}; border-bottom: 2px solid {focus}; }}
            #quietBar QToolButton:disabled {{ color: {_col(c, "text_disabled")}; }}
            #quietBar QToolButton::menu-indicator {{ image: none; width: 0; }}
            #quietBar QToolButton[primary="true"] {{ background: {focus}; color: {on_focus}; padding: 4px 12px;
                margin-left: 6px; font-weight: 600; }}
            #quietBar QToolButton[primary="true"]:hover {{ background: {focus}; }}
        """)

    # ---- internals --------------------------------------------------------------------------
    def _style_icon_button(self, btn: QToolButton) -> None:
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        btn.setAutoRaise(True)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
