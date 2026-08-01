"""Read-only FleetBus display for the land-air collaboration D task."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from components.d_task_status import (
    drone_task_program_active,
    operation_state_label,
)
from components.fleet_models import NodeFlags
from components.ui.map_widget import FleetMapWidget


class AutoFitLabel(QLabel):
    """Keep one line of text at the largest size that fits the label."""

    def __init__(self, text="", parent=None, minimum_point_size=14, maximum_point_size=64):
        super().__init__(text, parent)
        self._minimum_point_size = int(minimum_point_size)
        self._maximum_point_size = int(maximum_point_size)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def setText(self, text):
        super().setText(text)
        self._fit_font()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_font()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_font()

    def _fit_font(self):
        text = self.text()
        bounds = self.contentsRect()
        available_width = max(1, bounds.width() - 16)
        available_height = max(1, bounds.height() - 12)
        low = self._minimum_point_size
        high = self._maximum_point_size
        best = low
        while low <= high:
            size = (low + high) // 2
            font = self.font()
            font.setPointSize(size)
            font.setBold(True)
            metrics = QFontMetrics(font)
            if (
                metrics.horizontalAdvance(text) <= available_width
                and metrics.height() <= available_height
            ):
                best = size
                low = size + 1
            else:
                high = size - 1
        font = self.font()
        font.setPointSize(best)
        font.setBold(True)
        self.setFont(font)


class TrackingNodePanel(QFrame):
    """Present one endpoint snapshot; this widget has no command controls."""

    def __init__(self, title, node_role, parent=None):
        super().__init__(parent)
        self.setObjectName("nodePanel")
        self._title = QLabel(title, objectName="panelTitle")
        self._link = QLabel("离线")
        self._local_position = QLabel("--")
        self._position = QLabel("--")
        self._height = QLabel("--")
        self._heading = QLabel("--")
        self._battery = QLabel("--")

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        layout.addWidget(self._title)
        layout.addLayout(form)

    def update_snapshot(self, node, task_active=None):
        if task_active is False:
            link = "离线"
            color = "#b4232c"
        elif task_active is True:
            link = "在线"
            color = "#16805b"
        elif not node.online or node.stale:
            link = "离线"
            color = "#b4232c"
        else:
            link = "在线"
            color = "#16805b"
        self._link.setText(link)
        self._link.setStyleSheet("color: {}; font-weight: 700;".format(color))

        if node.node_flags & int(NodeFlags.POSE_VALID):
            self._local_position.setText(
                "x={:.0f}, y={:.0f} cm".format(
                    node.x_cm,
                    node.y_cm,
                )
            )
        else:
            self._local_position.setText("等待有效本地坐标")

        pose = node.world_pose
        if pose is None:
            self._position.setText("--")
            self._height.setText("--")
            self._heading.setText("--")
        else:
            self._position.setText(
                "({:.0f}, {:.0f}) cm".format(pose.x_cm, pose.y_cm)
            )
            self._height.setText("{:.0f} cm".format(pose.z_cm))
            self._heading.setText("{:.1f}° CCW".format(pose.heading_deg))
        self._battery.setText("{:.2f} V".format(node.battery_cV / 100.0))


class DTaskMainWindow(QMainWindow):
    """Map plus status panels for the first two land-air collaboration tasks."""

    def __init__(
        self,
        field_config=None,
        display_geometry=None,
        coordinate_frames_confirmed=False,
        parent=None,
        trajectory_minimum_quality=None,
    ):
        super().__init__(parent)
        field_config = field_config or {}
        self.setWindowTitle("陆空协同无人机系统 - 地面站")
        self.setMinimumSize(900, 500)
        self.resize(1000, 560)

        self.map = FleetMapWidget(
            field_width_cm=field_config.get("width_cm", 400.0),
            field_height_cm=field_config.get("height_cm", 500.0),
            display_geometry=display_geometry or {},
            field_markers=field_config.get("markers", {}),
            competition_track=field_config.get("competition_track", {}),
            launch_point=field_config.get("launch_point"),
            trajectory_minimum_quality=trajectory_minimum_quality,
        )
        self.drone_panel = TrackingNodePanel("无人机", "drone")
        self.car_panel = TrackingNodePanel("循线小车", "car")
        self._radar_distance = QLabel("雷达中心距 A：20 cm")
        self._radar_distance.setObjectName("radarDistance")
        self._radar_distance.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._drone_mission = AutoFitLabel("未上报")
        self._drone_mission.setObjectName("droneMission")
        self._drone_mission.setMinimumHeight(90)
        side = QVBoxLayout()
        side.setSpacing(6)
        side.addWidget(self._radar_distance)
        side.addWidget(self.drone_panel)
        side.addWidget(self.car_panel)
        side.addWidget(self._drone_mission, 1)

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
            QLabel#panelTitle { color: #1e2933; font-size: 20px; font-weight: 700; }
            QFrame#nodePanel QLabel { font-size: 15px; }
            QLabel#radarDistance { color: #d32f2f; font-size: 16px;
                                   font-weight: 700; padding: 0 4px 0 0; }
            QLabel#droneMission { background: #eaf2ff; color: #174ea6;
                                  border: 2px solid #8ab4f8; border-radius: 8px;
                                  padding: 8px; font-weight: 800; }
            """
        )

    def update_snapshot(self, snapshot):
        task_active = drone_task_program_active(
            snapshot.drone.operation_state,
            snapshot.drone.session,
        )
        self.map.set_snapshot(snapshot)
        self.drone_panel.update_snapshot(snapshot.drone, task_active)
        self.car_panel.update_snapshot(snapshot.car, task_active)
        distance = snapshot.car.radar_center_behind_a_centi_cm
        if distance is not None:
            value = distance / 100.0
            self._radar_distance.setText(
                "雷达中心距 A：{:g} cm".format(value)
            )
        self._drone_mission.setText(
            operation_state_label(snapshot.drone.operation_state, "drone")
        )
