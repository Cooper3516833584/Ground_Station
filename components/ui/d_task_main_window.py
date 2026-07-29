"""Read-only FleetBus display for the land-air collaboration D task."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from components.d_task_status import operation_state_label
from components.ui.map_widget import FleetMapWidget


class TrackingNodePanel(QFrame):
    """Present one endpoint snapshot; this widget has no command controls."""

    def __init__(self, title, node_role, parent=None):
        super().__init__(parent)
        self.setObjectName("nodePanel")
        self._title = QLabel(title, objectName="panelTitle")
        self._node_role = node_role
        self._link = QLabel("离线")
        self._position = QLabel("--")
        self._height = QLabel("--")
        self._heading = QLabel("--")
        self._battery = QLabel("--")
        self._phase = QLabel("未上报")
        self._phase.setWordWrap(True)

        form = QFormLayout()
        form.addRow("链路", self._link)
        form.addRow("场地位置", self._position)
        form.addRow("高度", self._height)
        form.addRow("航向", self._heading)
        form.addRow("电池", self._battery)
        form.addRow("任务状态", self._phase)

        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addLayout(form)

    def update_snapshot(self, node):
        if not node.online:
            link = "离线"
            color = "#b4232c"
        elif node.stale:
            link = "数据超时"
            color = "#a36b00"
        else:
            link = "在线"
            color = "#16805b"
        self._link.setText(link)
        self._link.setStyleSheet("color: {}; font-weight: 700;".format(color))

        pose = node.world_pose
        if pose is None:
            self._position.setText("等待有效 FIELD 坐标")
            self._height.setText("--")
            self._heading.setText("--")
        else:
            suffix = "（陈旧）" if node.stale else ""
            self._position.setText(
                "({:.0f}, {:.0f}) cm{}".format(pose.x_cm, pose.y_cm, suffix)
            )
            self._height.setText("{:.0f} cm".format(pose.z_cm))
            self._heading.setText("{:.1f}° CCW".format(pose.heading_deg))
        self._battery.setText("{:.2f} V".format(node.battery_cV / 100.0))
        self._phase.setText(
            operation_state_label(node.operation_state, self._node_role)
        )


class DTaskMainWindow(QMainWindow):
    """Map plus status panels for the first two land-air collaboration tasks."""

    def __init__(
        self,
        field_config=None,
        display_geometry=None,
        coordinate_frames_confirmed=False,
        parent=None,
    ):
        super().__init__(parent)
        field_config = field_config or {}
        self.setWindowTitle("陆空协同无人机系统 - 地面站")
        self.setMinimumSize(960, 600)
        self.resize(1180, 720)

        self.map = FleetMapWidget(
            field_width_cm=field_config.get("width_cm", 400.0),
            field_height_cm=field_config.get("height_cm", 500.0),
            display_geometry=display_geometry or {},
            field_markers=field_config.get("markers", {}),
        )
        self.drone_panel = TrackingNodePanel("无人机", "drone")
        self.car_panel = TrackingNodePanel("循线小车", "car")
        self._relay = QLabel("FleetBus：等待端点响应")
        self._relay.setObjectName("relayStatus")
        self._coordinate_warning = QLabel()
        self._coordinate_warning.setObjectName("coordinateWarning")
        if coordinate_frames_confirmed:
            self._coordinate_warning.setText("FIELD 坐标变换：已现场确认")
        else:
            self._coordinate_warning.setText(
                "FIELD 坐标变换：待确认（显示仅供联调，不用于控制）"
            )
        note = QLabel(
            "仅显示与轮询：地面站不会从本界面下发起飞、移动、抛投或降落指令。"
        )
        note.setWordWrap(True)
        note.setObjectName("note")

        side = QVBoxLayout()
        side.addWidget(self._relay)
        side.addWidget(self._coordinate_warning)
        side.addWidget(self.drone_panel)
        side.addWidget(self.car_panel)
        side.addWidget(note)
        side.addStretch(1)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.map, 3)
        layout.addLayout(side, 2)
        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QWidget { font-family: 'DejaVu Sans', 'Microsoft YaHei', sans-serif; }
            QFrame#nodePanel { background: #ffffff; border: 1px solid #d8dde3;
                                border-radius: 6px; padding: 6px; }
            QLabel#panelTitle { color: #1e2933; font-size: 18px; font-weight: 700; }
            QLabel#relayStatus { background: #243b53; color: white; padding: 10px;
                                 border-radius: 6px; font-weight: 700; }
            QLabel#coordinateWarning { color: #8a5d00; font-weight: 700; }
            QLabel#note { color: #52606d; padding: 8px; }
            """
        )

    def update_snapshot(self, snapshot):
        self.map.set_snapshot(snapshot)
        self.drone_panel.update_snapshot(snapshot.drone)
        self.car_panel.update_snapshot(snapshot.car)
        online = []
        if snapshot.drone.online:
            online.append("无人机")
        if snapshot.car.online:
            online.append("小车")
        if online:
            self._relay.setText("FleetBus 正在轮询：{}".format("、".join(online)))
        else:
            self._relay.setText("FleetBus：等待端点响应")
