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
        self._local_pose = QLabel("--")
        self._field_pose = QLabel("未配置/无有效 FIELD 位姿")
        self._battery = QLabel("--")
        self._quality = QLabel("--")
        self._operation = QLabel("--")
        self._command = QLabel("--")
        self._error = QLabel("--")
        form = QFormLayout()
        form.addRow("链路", self._link)
        form.addRow("本地位置", self._local_pose)
        form.addRow("场地位置", self._field_pose)
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
        self._local_pose.setText(
            "({:.0f}, {:.0f}, {:.0f}; {:.2f}° CCW)".format(
                snapshot.x_cm,
                snapshot.y_cm,
                snapshot.z_cm,
                snapshot.heading_cdeg / 100.0,
            )
        )
        world_pose = snapshot.world_pose
        if world_pose is None:
            self._field_pose.setText("未配置/无有效 FIELD 位姿")
        else:
            self._field_pose.setText(
                "({:.1f}, {:.1f}, {:.1f}; {:.2f}° CCW)".format(
                    world_pose.x_cm,
                    world_pose.y_cm,
                    world_pose.z_cm,
                    world_pose.heading_deg,
                )
            )
        self._battery.setText("{:.2f} V".format(snapshot.battery_cV / 100.0))
        self._quality.setText(str(snapshot.pose_quality))
        self._operation.setText(str(snapshot.operation_state))
        self._command.setText(
            "{} / {}".format(
                snapshot.active_command_seq, snapshot.active_command_status
            )
        )
        self._error.setText(str(snapshot.error_code))
