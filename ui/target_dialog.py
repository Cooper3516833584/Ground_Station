from __future__ import annotations

from PyQt5 import QtWidgets


class TargetDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select targets")

        self.target1 = QtWidgets.QSpinBox()
        self.target1.setRange(1, 12)
        self.target2 = QtWidgets.QSpinBox()
        self.target2.setRange(1, 12)
        self.target2.setValue(2)

        form = QtWidgets.QFormLayout()
        form.addRow("Target 1", self.target1)
        form.addRow("Target 2", self.target2)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def targets(self) -> tuple[int, int]:
        return self.target1.value(), self.target2.value()

    def _accept_if_valid(self) -> None:
        if self.target1.value() == self.target2.value():
            QtWidgets.QMessageBox.warning(self, "Invalid targets", "Targets must differ.")
            return
        self.accept()

