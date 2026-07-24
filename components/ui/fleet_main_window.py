"""Main FleetBus window; emits requests and never writes the serial link."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from components.fleet_models import CommandId, NodeFlags, NodeId
from components.ui.map_widget import FleetMapWidget
from components.ui.node_panel import NodePanel


def point_in_polygon(point, polygon):
    x_cm, y_cm = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y_cm) != (y2 > y_cm):
            crossing_x = (x2 - x1) * (y_cm - y1) / (y2 - y1) + x1
            if x_cm < crossing_x:
                inside = not inside
        previous = current
    return inside


class TargetDialog(QDialog):
    def __init__(self, x_cm, y_cm, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认目标")
        self.node = QComboBox()
        self.node.addItem("CAR", int(NodeId.CAR))
        self.node.addItem("DRONE", int(NodeId.DRONE))
        self.x = QSpinBox()
        self.y = QSpinBox()
        self.height = QSpinBox()
        self.heading = QDoubleSpinBox()
        self.heading_enabled = QCheckBox("指定最终航向")
        for field in (self.x, self.y, self.height):
            field.setRange(-100000, 100000)
        self.heading.setRange(0, 359.99)
        self.heading.setDecimals(2)
        self.heading.setEnabled(False)
        self.heading_enabled.toggled.connect(self.heading.setEnabled)
        self.x.setValue(round(x_cm))
        self.y.setValue(round(y_cm))
        self.height.setValue(100)
        form = QFormLayout(self)
        form.addRow("节点", self.node)
        form.addRow("X cm", self.x)
        form.addRow("Y cm", self.y)
        form.addRow("无人机高度 cm", self.height)
        form.addRow(self.heading_enabled)
        form.addRow("最终航向 °CCW", self.heading)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class FleetMainWindow(QMainWindow):
    command_requested = pyqtSignal(int, int, object)
    stop_all_requested = pyqtSignal()
    map_requested = pyqtSignal(int)
    path_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FleetBus Ground Station")
        self._snapshot = None
        self.map = FleetMapWidget()
        self.drone_panel = NodePanel("无人机", supports_hold=True)
        self.car_panel = NodePanel("小车")
        self.sync_status = QLabel("坐标同步：未知")

        self.origin_x = QSpinBox()
        self.origin_y = QSpinBox()
        self.origin_heading = QDoubleSpinBox()
        for field in (self.origin_x, self.origin_y):
            field.setRange(-100000, 100000)
        self.origin_heading.setRange(0, 359.99)
        sync_form = QFormLayout()
        sync_form.addRow("小车起点世界 X cm", self.origin_x)
        sync_form.addRow("小车起点世界 Y cm", self.origin_y)
        sync_form.addRow("小车启动 +X 世界航向 °CCW", self.origin_heading)
        sync_button = QPushButton("同步小车坐标")
        sync_button.clicked.connect(self._sync_coordinate)
        sync_form.addRow(self.sync_status)
        sync_form.addRow(sync_button)

        stop_all = QPushButton("依次停止全部设备")
        stop_all.clicked.connect(self.stop_all_requested)
        map_button = QPushButton("请求小车地图")
        map_button.clicked.connect(lambda: self.map_requested.emit(int(NodeId.CAR)))
        path_button = QPushButton("请求小车路径")
        path_button.clicked.connect(lambda: self.path_requested.emit(int(NodeId.CAR)))

        side = QVBoxLayout()
        side.addWidget(self.drone_panel)
        side.addWidget(self.car_panel)
        side.addLayout(sync_form)
        side.addWidget(map_button)
        side.addWidget(path_button)
        side.addWidget(stop_all)
        layout = QHBoxLayout()
        layout.addWidget(self.map, 3)
        layout.addLayout(side, 2)
        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

        self.map.target_clicked.connect(self._target_clicked)
        self.drone_panel.stop_requested.connect(
            lambda: self._simple_command(NodeId.DRONE, CommandId.TARGETED_STOP)
        )
        self.drone_panel.hold_requested.connect(
            lambda: self._simple_command(NodeId.DRONE, CommandId.DRONE_HOLD)
        )
        self.drone_panel.cancel_requested.connect(
            lambda: self._simple_command(NodeId.DRONE, CommandId.CANCEL_TASK)
        )
        self.car_panel.stop_requested.connect(
            lambda: self._simple_command(NodeId.CAR, CommandId.TARGETED_STOP)
        )
        self.car_panel.cancel_requested.connect(
            lambda: self._simple_command(NodeId.CAR, CommandId.TARGETED_STOP)
        )

    def update_snapshot(self, snapshot):
        self._snapshot = snapshot
        self.drone_panel.update_snapshot(snapshot.drone)
        self.car_panel.update_snapshot(snapshot.car)
        self.map.set_snapshot(snapshot)
        synced = bool(
            snapshot.car.node_flags & int(NodeFlags.COORDINATE_FRAME_SYNCED)
        )
        self.sync_status.setText(
            "坐标同步：{}".format("已完成" if synced else "未完成")
        )

    def _simple_command(self, node_id, command_id):
        self.command_requested.emit(int(node_id), int(command_id), None)

    def _sync_coordinate(self):
        body = (
            self.origin_x.value(),
            self.origin_y.value(),
            round(self.origin_heading.value() * 100),
        )
        self.command_requested.emit(
            int(NodeId.CAR), int(CommandId.SET_COORDINATE_FRAME), body
        )

    def _target_clicked(self, x_cm, y_cm):
        dialog = TargetDialog(x_cm, y_cm, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        node_id = dialog.node.currentData()
        target = (dialog.x.value(), dialog.y.value())
        if node_id == int(NodeId.CAR) and not self._car_accepts(target):
            return
        body = (
            target[0],
            target[1],
            dialog.height.value(),
            (
                round(dialog.heading.value() * 100)
                if dialog.heading_enabled.isChecked()
                else None
            ),
        )
        command_id = (
            CommandId.CAR_NAVIGATE_TO
            if node_id == int(NodeId.CAR)
            else CommandId.DRONE_GOTO
        )
        self.map.set_target(node_id, *target)
        self.command_requested.emit(node_id, int(command_id), body)

    def _car_accepts(self, target):
        if self._snapshot is None:
            return False
        car = self._snapshot.car
        required = (
            NodeFlags.MAP_READY
            | NodeFlags.COORDINATE_FRAME_SYNCED
            | NodeFlags.POSE_VALID
        )
        if not car.online or car.stale or (car.node_flags & int(required)) != int(required):
            QMessageBox.warning(self, "无法下发", "小车链路、地图、坐标同步或定位未就绪。")
            return False
        if car.active_command_status in (1, 2):
            QMessageBox.warning(self, "无法下发", "小车当前正在处理其他命令。")
            return False
        if len(car.map_corners) != 4 or not point_in_polygon(target, car.map_corners):
            QMessageBox.warning(self, "无法下发", "目标不在小车上报的场地多边形内。")
            return False
        return True
