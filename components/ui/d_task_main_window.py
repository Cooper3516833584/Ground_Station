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
from components.fleet_models import NodeFlags
from components.ui.map_widget import FleetMapWidget


class TrackingNodePanel(QFrame):
    """Present one endpoint snapshot; this widget has no command controls."""

    def __init__(self, title, node_role, parent=None):
        super().__init__(parent)
        self.setObjectName("nodePanel")
        self._title = QLabel(title, objectName="panelTitle")
        self._node_role = node_role
        self._link = QLabel("离线")
        self._local_position = QLabel("--")
        self._position = QLabel("--")
        self._height = QLabel("--")
        self._heading = QLabel("--")
        self._battery = QLabel("--")
        self._phase = QLabel("未上报")
        self._phase.setWordWrap(True)

        form = QFormLayout()
        form.addRow(
            "相对起点位置" if node_role == "car" else "端点本地位置",
            self._local_position,
        )
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(1)
        form.setHorizontalSpacing(5)
        form.addRow("链路", self._link)
        form.addRow("场地位置", self._position)
        if node_role != "car":
            form.addRow("高度", self._height)
        form.addRow("航向", self._heading)
        if node_role != "car":
            form.addRow("电池", self._battery)
            form.addRow("任务状态", self._phase)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
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

        if node.node_flags & int(NodeFlags.POSE_VALID):
            suffix = "（陈旧）" if node.stale else ""
            self._local_position.setText(
                "x={:.0f}, y={:.0f} cm{}".format(
                    node.x_cm,
                    node.y_cm,
                    suffix,
                )
            )
        else:
            self._local_position.setText("等待有效本地坐标")

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
        self.setMinimumSize(760, 430)
        self.resize(780, 440)

        self.map = FleetMapWidget(
            field_width_cm=field_config.get("width_cm", 400.0),
            field_height_cm=field_config.get("height_cm", 500.0),
            display_geometry=display_geometry or {},
            field_markers=field_config.get("markers", {}),
            competition_track=field_config.get("competition_track", {}),
            launch_point=field_config.get("launch_point"),
        )
        self.drone_panel = TrackingNodePanel("无人机", "drone")
        self.car_panel = TrackingNodePanel("循线小车", "car")
        self._relay = QLabel("FleetBus：等待端点响应")
        self._relay.setObjectName("relayStatus")
        side = QVBoxLayout()
        side.setSpacing(3)
        side.addWidget(self._relay)
        side.addWidget(self.drone_panel)
        side.addWidget(self.car_panel)
        side.addStretch(1)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(6)
        layout.addWidget(self.map, 3)
        layout.addLayout(side, 2)
        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QWidget { font-family: 'DejaVu Sans', 'Microsoft YaHei', sans-serif; }
            QFrame#nodePanel { background: #ffffff; border: 1px solid #d8dde3;
                                border-radius: 5px; padding: 2px; }
            QLabel#panelTitle { color: #1e2933; font-size: 16px; font-weight: 700; }
            QLabel#relayStatus { background: #243b53; color: white; padding: 5px;
                                 border-radius: 6px; font-weight: 700; }
            QFrame#nodePanel QLabel { font-size: 12px; }
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
