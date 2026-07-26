"""Main FleetBus window; emits requests and never writes the serial link."""

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from components.fleet_models import CommandId, NodeFlags, NodeId, SurveyFlags, TerrainCode
from components.ui.map_widget import FleetMapWidget
from components.ui.node_panel import NodePanel


TERRAIN_NAMES = {
    int(TerrainCode.UNKNOWN): "?",
    int(TerrainCode.SNOW_MOUNTAIN): "雪山",
    int(TerrainCode.FIELD): "田野",
    int(TerrainCode.RIVER): "河流",
    int(TerrainCode.SETTLEMENTS): "居民地",
    int(TerrainCode.LAKE): "湖泊",
    int(TerrainCode.DEBRIS_FLOW): "泥石流",
    int(TerrainCode.WILDFIRE): "山火",
}
TERRAIN_IMAGE_FILES = {
    int(TerrainCode.SNOW_MOUNTAIN): "snow_mountain.png",
    int(TerrainCode.FIELD): "field.png",
    int(TerrainCode.RIVER): "river.png",
    int(TerrainCode.SETTLEMENTS): "settlements.png",
    int(TerrainCode.LAKE): "lake.png",
    int(TerrainCode.DEBRIS_FLOW): "debris_flow.png",
    int(TerrainCode.WILDFIRE): "wildfire.png",
}


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

    def __init__(self, parent=None, terrain_image_dir=None):
        super().__init__(parent)
        self.setWindowTitle("FleetBus Ground Station")
        self._snapshot = None
        self.map = FleetMapWidget()
        self.drone_panel = NodePanel("无人机", supports_hold=True)
        self.car_panel = NodePanel("小车")
        self.sync_status = QLabel("坐标同步：未知")
        self.survey_status = QLabel("测绘状态：等待无人机")
        self.disaster_status = QLabel("灾害：未发现")
        self.survey_cells = []
        self.survey_coordinate_labels = []
        image_dir = Path(terrain_image_dir) if terrain_image_dir else None
        self._terrain_pixmaps = {
            code: QPixmap(str(image_dir / filename))
            for code, filename in TERRAIN_IMAGE_FILES.items()
        } if image_dir is not None else {}
        survey_layout = QGridLayout()
        for row in range(3):
            row_cells = []
            row_coordinates = []
            for col in range(5):
                cell = QLabel()
                cell.setAlignment(Qt.AlignCenter)
                cell.setFixedSize(104, 78)
                cell.setStyleSheet("border: 1px solid #777; background: #f5f5f5;")
                coordinate = QLabel()
                coordinate.setAlignment(Qt.AlignCenter)
                coordinate.setStyleSheet("color: #555; font-size: 10px;")
                holder = QWidget()
                holder_layout = QVBoxLayout(holder)
                holder_layout.setContentsMargins(0, 0, 0, 0)
                holder_layout.setSpacing(2)
                holder_layout.addWidget(cell)
                holder_layout.addWidget(coordinate)
                # Survey row 0 is the lowest field row, so draw it at the bottom.
                survey_layout.addWidget(holder, 2 - row, col)
                row_cells.append(cell)
                row_coordinates.append(coordinate)
            self.survey_cells.append(row_cells)
            self.survey_coordinate_labels.append(row_coordinates)
        survey_group = QGroupBox("无人机 3×5 地形测绘")
        survey_group.setLayout(survey_layout)

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
        side.addWidget(self.survey_status)
        side.addWidget(self.disaster_status)
        side.addWidget(survey_group)
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
        complete = bool(snapshot.drone.survey_flags & int(SurveyFlags.COMPLETE))
        self.survey_status.setText(
            "测绘状态：{}（版本 {}）".format(
                "已完成" if complete else "进行中", snapshot.drone.survey_revision
            )
        )
        for index, terrain in enumerate(snapshot.drone.terrain_codes):
            row, col = divmod(index, 5)
            cell = self.survey_cells[row][col]
            coordinate = self.survey_coordinate_labels[row][col]
            positions = snapshot.drone.survey_cell_positions_cm
            if len(positions) == 15:
                x_cm, y_cm = positions[index]
                coordinate.setText(f"({x_cm}, {y_cm}) cm")
                cell.setToolTip(
                    f"{TERRAIN_NAMES.get(int(terrain), '未知')} ({x_cm}, {y_cm}) cm"
                )
            else:
                coordinate.clear()
                cell.setToolTip(TERRAIN_NAMES.get(int(terrain), "未知"))
            # Always clear first: UNKNOWN and missing assets must never retain
            # a pixmap from the previous survey revision.
            cell.clear()
            pixmap = self._terrain_pixmaps.get(int(terrain))
            if terrain != int(TerrainCode.UNKNOWN) and pixmap is not None and not pixmap.isNull():
                cell.setPixmap(
                    pixmap.scaled(
                        cell.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                )
            if terrain == int(TerrainCode.WILDFIRE):
                cell.setStyleSheet("border: 3px solid #ff2020; background: #ffb0a8;")
            elif terrain == int(TerrainCode.DEBRIS_FLOW):
                cell.setStyleSheet("border: 3px solid #ffb000; background: #ffe4a0;")
            elif terrain in (int(TerrainCode.RIVER), int(TerrainCode.LAKE)):
                cell.setStyleSheet("border: 1px solid #2474ff; background: #b9d9ff;")
            else:
                cell.setStyleSheet("border: 1px solid #777; background: #f5f5f5;")
        notices = []
        if snapshot.drone.wildfire_event_id:
            notices.append(
                "山火：第{}行第{}列".format(
                    snapshot.drone.wildfire_row + 1,
                    snapshot.drone.wildfire_col + 1,
                )
            )
        if snapshot.drone.debris_event_id:
            notices.append(
                "泥石流：第{}行第{}列".format(
                    snapshot.drone.debris_row + 1,
                    snapshot.drone.debris_col + 1,
                )
            )
        self.disaster_status.setText("灾害：" + ("；".join(notices) if notices else "未发现"))
        self.disaster_status.setStyleSheet(
            "font-weight: bold; color: #d00000;" if notices else ""
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
