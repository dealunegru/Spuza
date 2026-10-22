"""Preferences sheet: the prefetch window (pictures ready ahead / kept behind) and what it costs in memory.

The sheet only edits; the window saves and applies on accept. ``values()`` is the pair to use,
``memory_text()`` the plain text of the live memory line. Advanced mode adds three rows (decode threads,
peaking threshold, remember marks); they are hidden, and the classic sheet is exactly as shipped, until the
Advanced box is ticked.

Three classic rows (always visible) say what a copy does: organise the destination into raw / soc / ed,
eject the card afterwards, quit Spuza afterwards. ``seeded_*`` is what each started as, so the window saves
only a changed value. A fourth row, Master folder, names a folder every copy goes under (``master_folder()``, "" = not set; the window
saves it only when it differs from ``seeded_master``); Reset to defaults leaves it alone.
Reset to defaults sets them back to on (the shipped default) along with the spins.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
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
                 advanced: bool = False, threads: int = settings.DEFAULT_THREADS,
                 peaking_threshold: int = settings.DEFAULT_PEAKING_THRESHOLD, resume: bool = True,
                 organise: bool = True, eject_after: bool = True, quit_after: bool = True,
                 write_rotation: bool = True, zoom_full_sensor: bool = False, master_folder: str = ""):
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
        self.seeded_advanced = bool(advanced)  # what the box started as: the window saves a mode change only
        self.advanced_check.setChecked(self.seeded_advanced)
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(*settings.THREADS_RANGE)
        self.threads_spin.setValue(threads)
        self.threads_label = QLabel("Decode threads")
        self.peaking_label = QLabel("Peaking threshold")
        self.peaking_spin = QSpinBox()
        self.peaking_spin.setRange(*settings.PEAKING_THRESHOLD_RANGE)
        self.peaking_spin.setValue(peaking_threshold)
        self.seeded_peaking = self.peaking_spin.value()  # what the rows started as: the window saves a change only
        self.resume_check = QCheckBox("Remember marks between runs")
        self.seeded_resume = bool(resume)
        self.resume_check.setChecked(self.seeded_resume)
        self.organise_check = QCheckBox("Copy into raw / soc / ed folders")
        self.eject_check = QCheckBox("Eject the card after copying")
        self.quit_check = QCheckBox("Quit Spuza after copying")
        self.seeded_organise, self.seeded_eject, self.seeded_quit = bool(organise), bool(eject_after), bool(quit_after)
        self.rotation_check = QCheckBox("Write rotation to copies (EXIF)")
        self.seeded_rotation = bool(write_rotation)
        self.rotation_check.setChecked(self.seeded_rotation)
        self.sensor_check = QCheckBox("Zoom shows the full sensor on cropped shots")
        self.seeded_sensor = bool(zoom_full_sensor)
        self.sensor_check.setChecked(self.seeded_sensor)
        self.organise_check.setChecked(self.seeded_organise)
        self.eject_check.setChecked(self.seeded_eject)
        self.quit_check.setChecked(self.seeded_quit)
        self.seeded_master = str(master_folder or "")
        self._master = self.seeded_master
        self.choose_folder: Callable = lambda parent, title, start: QFileDialog.getExistingDirectory(parent, title, start)
        self.master_title = QLabel("Master folder")
        self.master_label = QLabel()
        self.master_label.setWordWrap(True)
        self.master_choose = QPushButton("Choose…")
        self.master_choose.setAutoDefault(False)
        self.master_choose.setToolTip("Choose a master folder  Space")  # the focused button is pressed with Space
        self.master_choose.clicked.connect(self._pick_master)
        self.master_clear = QPushButton("Clear")
        self.master_clear.setAutoDefault(False)
        self.master_clear.setToolTip("Clear the master folder  Space")
        self.master_clear.clicked.connect(self._clear_master)
        self._show_master()
        self.note_label = QLabel("The ADVANCED marker changes now; decode threads apply at the next launch.")
        self.note_label.setWordWrap(True)
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.setAutoDefault(False)
        self.reset_button.clicked.connect(self.reset_to_defaults)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        for std, tip in ((QDialogButtonBox.StandardButton.Ok, "OK  Enter"),
                         (QDialogButtonBox.StandardButton.Cancel, "Cancel  Esc")):
            self.buttons.button(std).setToolTip(tip)  # names its key (design policy)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout()
        self.ahead_label, self.behind_label = QLabel("Pictures ready ahead"), QLabel("Pictures kept behind")
        form.addRow(self.ahead_label, self.ahead_spin)
        form.addRow(self.behind_label, self.behind_spin)
        form.addRow(self.threads_label, self.threads_spin)
        form.addRow(self.peaking_label, self.peaking_spin)
        form.addRow(self.resume_check)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.memory_label)
        lay.addWidget(self.organise_check)
        lay.addWidget(self.eject_check)
        lay.addWidget(self.quit_check)
        lay.addWidget(self.rotation_check)
        lay.addWidget(self.sensor_check)
        master_row = QHBoxLayout()
        master_row.addWidget(self.master_title)
        master_row.addWidget(self.master_label, 1)
        master_row.addWidget(self.master_choose)
        master_row.addWidget(self.master_clear)
        lay.addLayout(master_row)
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
        self.peaking_label.setVisible(on)
        self.peaking_spin.setVisible(on)
        self.resume_check.setVisible(on)
        self.note_label.setVisible(on)

    def master_folder(self) -> str:
        return self._master

    def _show_master(self) -> None:
        self.master_label.setText(self._master or "Not set: copies go to the usual folder")
        self.master_clear.setEnabled(bool(self._master))

    def _pick_master(self) -> None:
        chosen = self.choose_folder(self, "Choose the master folder", self._master)
        if chosen:
            self._master = str(chosen)
            self._show_master()

    def _clear_master(self) -> None:
        self._master = ""
        self._show_master()

    def advanced(self) -> bool:
        return self.advanced_check.isChecked()

    def threads(self) -> int:
        return self.threads_spin.value()

    def peaking_threshold(self) -> int:
        return self.peaking_spin.value()

    def resume(self) -> bool:
        return self.resume_check.isChecked()

    def organise(self) -> bool:
        return self.organise_check.isChecked()

    def eject_after(self) -> bool:
        return self.eject_check.isChecked()

    def quit_after(self) -> bool:
        return self.quit_check.isChecked()

    def write_rotation(self) -> bool:
        return self.rotation_check.isChecked()

    def zoom_full_sensor(self) -> bool:
        return self.sensor_check.isChecked()

    def values(self) -> tuple[int, int]:
        return self.ahead_spin.value(), self.behind_spin.value()

    def memory_text(self) -> str:
        return self.memory_label.text()

    def reset_to_defaults(self) -> None:
        """Set the spins to the defaults; nothing is saved until OK. The mode checkbox and threads are left alone
        (a mode is not a tuning value); the four copy rows go back to on, their shipped default; the zoom row goes back to off."""
        self.ahead_spin.setValue(settings.DEFAULT_AHEAD)
        self.behind_spin.setValue(settings.DEFAULT_BEHIND)
        self.organise_check.setChecked(True)
        self.eject_check.setChecked(True)
        self.quit_check.setChecked(True)
        self.rotation_check.setChecked(True)
        self.sensor_check.setChecked(False)

    def _update_memory(self) -> None:
        a, b = self.values()
        self.memory_label.setText(f"about {settings.window_mb(a, b, self.max_side)} MB of previews")
