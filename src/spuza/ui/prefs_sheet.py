"""Preferences sheet: the prefetch window (pictures ready ahead / kept behind) and what it costs in memory.

The sheet only edits; the window saves and applies on accept. ``values()`` is the pair to use,
``memory_text()`` the plain text of the live memory line.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QCheckBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from spuza.ui import settings


class PrefsSheet(QDialog):
    def __init__(self, ahead: int, behind: int, max_side: int = 2560, parent=None, *,
                 advanced: bool = False, threads: int = settings.DEFAULT_THREADS):
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
        self.advanced_check = QCheckBox("Advanced mode: test features before they reach classic")
        self.advanced_check.setChecked(bool(advanced))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(*settings.THREADS_RANGE)
        self.threads_spin.setValue(threads)
        self.threads_label = QLabel("Decode threads")
        self.note_label = QLabel("The ADVANCED marker changes now; decode threads apply at the next launch.")
        self.note_label.setWordWrap(True)
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
        form.addRow(self.threads_label, self.threads_spin)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.memory_label)
        lay.addWidget(self.advanced_check)
        lay.addWidget(self.note_label)
        lay.addWidget(self.reset_button)
        lay.addWidget(self.buttons)

        self.ahead_spin.valueChanged.connect(self._update_memory)
        self.behind_spin.valueChanged.connect(self._update_memory)
        self._update_memory()
        self.advanced_check.toggled.connect(self._sync_advanced_rows)
        self._sync_advanced_rows(self.advanced_check.isChecked())

    def _sync_advanced_rows(self, on: bool) -> None:
        # setVisible(False) on a never-shown child stays hidden until shown; True shows with the sheet
        self.threads_label.setVisible(on)
        self.threads_spin.setVisible(on)
        self.note_label.setVisible(on)

    def advanced(self) -> bool:
        return self.advanced_check.isChecked()

    def threads(self) -> int:
        return self.threads_spin.value()

    def values(self) -> tuple[int, int]:
        return self.ahead_spin.value(), self.behind_spin.value()

    def memory_text(self) -> str:
        return self.memory_label.text()

    def reset_to_defaults(self) -> None:
        """Set the spins to the defaults; nothing is saved until OK. The mode checkbox and threads are left alone
        (a mode is not a tuning value)."""
        self.ahead_spin.setValue(settings.DEFAULT_AHEAD)
        self.behind_spin.setValue(settings.DEFAULT_BEHIND)

    def _update_memory(self) -> None:
        a, b = self.values()
        self.memory_label.setText(f"about {settings.window_mb(a, b, self.max_side)} MB of previews")
