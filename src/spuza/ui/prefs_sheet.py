"""Preferences sheet: the prefetch window (pictures ready ahead / kept behind) and what it costs in memory.

The sheet only edits; the window saves and applies on accept. ``values()`` is the pair to use,
``memory_text()`` the plain text of the live memory line.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from spuza.ui import settings


class PrefsSheet(QDialog):
    def __init__(self, ahead: int, behind: int, max_side: int = 2560, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.max_side = max_side
        self.ahead_spin = QSpinBox()
        self.ahead_spin.setRange(*settings.AHEAD_RANGE)
        self.behind_spin = QSpinBox()
        self.behind_spin.setRange(*settings.BEHIND_RANGE)
        self.ahead_spin.setValue(ahead)
        self.behind_spin.setValue(behind)
        self.memory_label = QLabel()
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.setAutoDefault(False)
        self.reset_button.clicked.connect(self.reset_to_defaults)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout()
        self.ahead_label, self.behind_label = QLabel("Pictures ready ahead"), QLabel("Pictures kept behind")
        form.addRow(self.ahead_label, self.ahead_spin)
        form.addRow(self.behind_label, self.behind_spin)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.memory_label)
        lay.addWidget(self.reset_button)
        lay.addWidget(self.buttons)

        self.ahead_spin.valueChanged.connect(self._update_memory)
        self.behind_spin.valueChanged.connect(self._update_memory)
        self._update_memory()

    def values(self) -> tuple[int, int]:
        return self.ahead_spin.value(), self.behind_spin.value()

    def memory_text(self) -> str:
        return self.memory_label.text()

    def reset_to_defaults(self) -> None:
        """Set the spins to the defaults; nothing is saved until OK."""
        self.ahead_spin.setValue(settings.DEFAULT_AHEAD)
        self.behind_spin.setValue(settings.DEFAULT_BEHIND)

    def _update_memory(self) -> None:
        a, b = self.values()
        self.memory_label.setText(f"about {settings.window_mb(a, b, self.max_side)} MB of previews")
