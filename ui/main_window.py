from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from models import Command, CommandId
from state_store import StateStore
from ui.target_dialog import TargetDialog


class MainWindow(QtWidgets.QMainWindow):
    set_targets_requested = QtCore.pyqtSignal(int, int)
    command_requested = QtCore.pyqtSignal(object)

    def __init__(self, store: StateStore):
        super().__init__()
        self._store = store
        self.setWindowTitle("Ground Station")
        self.setMinimumSize(1024, 600)
        self._labels: dict[str, QtWidgets.QLabel] = {}
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        for key, name in (
            ("link", "Link"),
            ("age", "Delay"),
            ("battery", "Battery"),
            ("mode", "Mode"),
            ("unlock", "Lock"),
        ):
            label = self._make_metric(name)
            self._labels[key] = label
            top.addWidget(label)
        layout.addLayout(top)

        grid = QtWidgets.QGridLayout()
        for row, (key, name) in enumerate(
            (
                ("attitude", "Attitude"),
                ("altitude", "Altitude"),
                ("velocity", "Velocity XYZ"),
                ("position", "Position XY"),
                ("mission", "Mission"),
                ("diag", "Diag"),
            )
        ):
            title = QtWidgets.QLabel(name)
            title.setStyleSheet("font-weight: 600; font-size: 18px;")
            value = QtWidgets.QLabel("--")
            value.setStyleSheet("font-size: 22px;")
            self._labels[key] = value
            grid.addWidget(title, row, 0)
            grid.addWidget(value, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid, stretch=1)

        actions = QtWidgets.QHBoxLayout()
        target_btn = QtWidgets.QPushButton("Targets")
        ping_btn = QtWidgets.QPushButton("Ping")
        start_btn = QtWidgets.QPushButton("Start")
        vision_btn = QtWidgets.QPushButton("Vision")
        stop_btn = QtWidgets.QPushButton("Stop")
        stop_btn.setObjectName("stopButton")
        stop_btn.setStyleSheet("#stopButton { background: #a31320; color: white; }")
        target_btn.clicked.connect(self._choose_targets)
        ping_btn.clicked.connect(lambda: self.command_requested.emit(Command(CommandId.PING)))
        start_btn.clicked.connect(
            lambda: self.command_requested.emit(Command(CommandId.START_MISSION))
        )
        vision_btn.clicked.connect(
            lambda: self.command_requested.emit(Command(CommandId.START_VISION_ACQUIRE))
        )
        stop_btn.clicked.connect(
            lambda: self.command_requested.emit(Command(CommandId.STOP_MISSION))
        )
        for button in (target_btn, ping_btn, start_btn, vision_btn, stop_btn):
            button.setMinimumHeight(48)
            actions.addWidget(button)
        layout.addLayout(actions)

        bottom = QtWidgets.QHBoxLayout()
        self._labels["alarm"] = QtWidgets.QLabel("")
        self._labels["last_command"] = QtWidgets.QLabel("")
        self._labels["last_ack"] = QtWidgets.QLabel("")
        bottom.addWidget(self._labels["alarm"], stretch=2)
        bottom.addWidget(self._labels["last_command"], stretch=1)
        bottom.addWidget(self._labels["last_ack"], stretch=1)
        layout.addLayout(bottom)

    def _make_metric(self, name: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(f"{name}: --")
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setMinimumHeight(44)
        label.setStyleSheet("font-size: 18px; padding: 6px; border: 1px solid #b8bec8;")
        return label

    def refresh(self) -> None:
        telemetry = self._store.telemetry
        stale = self._store.is_stale()
        link_text = "stale" if stale else ("online" if self._store.link.connected else "offline")
        self._labels["link"].setText(f"Link: {link_text}")
        age = self._store.telemetry_age()
        self._labels["age"].setText("Delay: --" if age is None else f"Delay: {age:.1f}s")
        if telemetry is None:
            for key in ("battery", "mode", "unlock", "attitude", "altitude", "velocity", "position", "diag"):
                self._labels[key].setText("--")
        else:
            self._labels["battery"].setText(f"Battery: {telemetry.battery_v:.2f}V")
            self._labels["mode"].setText(f"Mode: {telemetry.mode}")
            self._labels["unlock"].setText("Unlock: yes" if telemetry.unlock else "Unlock: no")
            self._labels["attitude"].setText(
                f"R {telemetry.roll_deg:.1f}  P {telemetry.pitch_deg:.1f}  Y {telemetry.yaw_deg:.1f}"
            )
            self._labels["altitude"].setText(
                f"Fused {telemetry.alt_fused_m:.2f} m  Add {telemetry.alt_add_m:.2f} m"
            )
            self._labels["velocity"].setText(
                f"{telemetry.vel_x_ms:.2f}, {telemetry.vel_y_ms:.2f}, {telemetry.vel_z_ms:.2f} m/s"
            )
            self._labels["position"].setText(
                f"{telemetry.pos_x_m:.2f}, {telemetry.pos_y_m:.2f} m"
            )
            self._labels["diag"].setText(
                f"cid {telemetry.cid}  cmd {telemetry.cmd_0}/{telemetry.cmd_1}"
            )
        self._labels["mission"].setText(self._store.mission.state.name)
        self._labels["alarm"].setText(self._store.link.alarm)
        self._labels["last_command"].setText(self._store.last_command)
        self._labels["last_ack"].setText(self._store.last_ack)

    def _choose_targets(self) -> None:
        dialog = TargetDialog(self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            target1, target2 = dialog.targets()
            self.set_targets_requested.emit(target1, target2)
