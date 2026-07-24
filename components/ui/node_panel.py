"""Compact status/control panel for one FleetBus node."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class NodePanel(QGroupBox):
    stop_requested = pyqtSignal()
    hold_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, title, supports_hold=False, parent=None):
        super().__init__(title, parent)
        self._link = QLabel("unknown")
        self._pose = QLabel("--")
        self._heading = QLabel("--")
        self._battery = QLabel("--")
        self._quality = QLabel("--")
        self._operation = QLabel("--")
        self._command = QLabel("--")
        self._error = QLabel("--")
        form = QFormLayout()
        form.addRow("链路", self._link)
        form.addRow("位置 cm", self._pose)
        form.addRow("航向", self._heading)
        form.addRow("电池", self._battery)
        form.addRow("定位质量", self._quality)
        form.addRow("运行状态", self._operation)
        form.addRow("活动命令", self._command)
        form.addRow("错误", self._error)

        buttons = QHBoxLayout()
        stop = QPushButton("定向停止")
        stop.clicked.connect(self.stop_requested)
        buttons.addWidget(stop)
        if supports_hold:
            hold = QPushButton("悬停")
            hold.clicked.connect(self.hold_requested)
            buttons.addWidget(hold)
        cancel = QPushButton("取消任务")
        cancel.clicked.connect(self.cancel_requested)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def update_snapshot(self, snapshot):
        self._link.setText(snapshot.link_status.value)
        self._pose.setText(
            "{:.0f}, {:.0f}, {:.0f}".format(
                snapshot.x_cm, snapshot.y_cm, snapshot.z_cm
            )
        )
        self._heading.setText("{:.2f}° CCW".format(snapshot.heading_cdeg / 100.0))
        self._battery.setText("{:.2f} V".format(snapshot.battery_cV / 100.0))
        self._quality.setText(str(snapshot.pose_quality))
        self._operation.setText(str(snapshot.operation_state))
        self._command.setText(
            "{} / {}".format(
                snapshot.active_command_seq, snapshot.active_command_status
            )
        )
        self._error.setText(str(snapshot.error_code))
