"""Fixed FIELD-frame QGraphicsView for FleetBus display."""

import math

from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QGraphicsScene, QGraphicsView

from components.fleet_models import NodeFlags, NodeId


class FleetMapWidget(QGraphicsView):
    target_clicked = pyqtSignal(float, float)

    def __init__(
        self,
        parent=None,
        field_width_cm=400.0,
        field_height_cm=500.0,
        display_geometry=None,
        field_markers=None,
        competition_track=None,
        launch_point=None,
    ):
        super().__init__(parent)
        self._field_width_cm = max(1.0, float(field_width_cm))
        self._field_height_cm = max(1.0, float(field_height_cm))
        self._display_geometry = display_geometry or {}
        self._field_markers = field_markers or {}
        self._competition_track = competition_track or {}
        self._launch_point = launch_point
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setMinimumSize(360, 340)
        self._snapshot = None
        self._targets = {}
        self._reset_scene_rect()

    def set_snapshot(self, snapshot):
        self._snapshot = snapshot
        self._redraw()

    def set_target(self, node_id, x_cm, y_cm):
        self._targets[int(node_id)] = (float(x_cm), float(y_cm))
        self._redraw()

    def mouseDoubleClickEvent(self, event):
        point = self.mapToScene(event.pos())
        self.target_clicked.emit(point.x(), -point.y())
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_to_field()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_to_field()

    def _reset_scene_rect(self):
        margin_cm = 35.0
        self._scene.setSceneRect(
            -margin_cm,
            -self._field_height_cm - margin_cm,
            self._field_width_cm + margin_cm * 2.0,
            self._field_height_cm + margin_cm * 2.0,
        )

    def _fit_to_field(self):
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    @staticmethod
    def _scene_point(x_cm, y_cm):
        return QPointF(float(x_cm), -float(y_cm))

    def _redraw(self):
        self._scene.clear()
        self._reset_scene_rect()
        self._draw_field()
        if self._snapshot is None:
            return
        trajectories = dict(self._snapshot.trajectories)
        self._draw_trajectory(
            trajectories.get(int(NodeId.DRONE), ()), QColor("#2979ff")
        )
        self._draw_trajectory(
            trajectories.get(int(NodeId.CAR), ()), QColor("#ff6d00")
        )
        car = self._snapshot.car
        if car.world_map_corners:
            polygon = QPolygonF(
                [self._scene_point(x_cm, y_cm) for x_cm, y_cm in car.world_map_corners]
            )
            self._scene.addPolygon(
                polygon, QPen(QColor("#2e7d32"), 2), QBrush(Qt.NoBrush)
            )
        self._draw_world_path(car.world_path_points, QColor("#2e7d32"))
        self._draw_node(self._snapshot.drone, QColor("#2979ff"), drone=True)
        self._draw_node(self._snapshot.car, QColor("#ff6d00"), drone=False)
        for node_id, (x_cm, y_cm) in self._targets.items():
            color = QColor("#2979ff" if node_id == NodeId.DRONE else "#ff6d00")
            point = self._scene_point(x_cm, y_cm)
            self._scene.addEllipse(
                point.x() - 6, point.y() - 6, 12, 12, QPen(color, 2), QBrush(Qt.NoBrush)
            )

    def _draw_field(self):
        boundary = QPen(QColor("#222222"), 2)
        self._scene.addRect(
            0.0, -self._field_height_cm, self._field_width_cm, self._field_height_cm,
            boundary, QBrush(Qt.NoBrush),
        )
        grid = QPen(QColor("#d8d8d8"), 0)
        for x_cm in range(0, int(self._field_width_cm) + 1, 50):
            self._scene.addLine(x_cm, 0, x_cm, -self._field_height_cm, grid)
        for y_cm in range(0, int(self._field_height_cm) + 1, 50):
            self._scene.addLine(0, -y_cm, self._field_width_cm, -y_cm, grid)
        self._draw_competition_track()
        self._draw_coordinate_indicator()
        for name, position in self._field_markers.items():
            if (
                isinstance(position, (str, bytes))
                or not hasattr(position, "__len__")
                or len(position) != 2
            ):
                continue
            point = self._scene_point(position[0], position[1])
            self._scene.addEllipse(
                point.x() - 4, point.y() - 4, 8, 8, QPen(Qt.darkMagenta, 2),
                QBrush(QColor("#d081ff")),
            )
            self._scene.addText(str(name)).setPos(point.x() + 5, point.y() - 18)
        self._draw_launch_point()

    def _draw_coordinate_indicator(self):
        """Show the D-task axes: +X follows A to B and +Y follows C to B."""
        origin = self._scene_point(self._field_width_cm - 25.0, 28.0)
        axis_length = 45.0
        pen = QPen(QColor("#263238"), 2)

        x_end = QPointF(origin.x(), origin.y() - axis_length)
        y_end = QPointF(origin.x() - axis_length, origin.y())
        self._scene.addLine(origin.x(), origin.y(), x_end.x(), x_end.y(), pen)
        self._scene.addLine(origin.x(), origin.y(), y_end.x(), y_end.y(), pen)
        self._scene.addLine(x_end.x(), x_end.y(), x_end.x() - 4, x_end.y() + 8, pen)
        self._scene.addLine(x_end.x(), x_end.y(), x_end.x() + 4, x_end.y() + 8, pen)
        self._scene.addLine(y_end.x(), y_end.y(), y_end.x() + 8, y_end.y() - 4, pen)
        self._scene.addLine(y_end.x(), y_end.y(), y_end.x() + 8, y_end.y() + 4, pen)
        self._scene.addText("X+ (A→B)").setPos(x_end.x() - 5, x_end.y() - 22)
        self._scene.addText("Y+ (C→B)").setPos(y_end.x() - 18, y_end.y() + 5)

    def _draw_competition_track(self):
        """Draw the fixed A-B-C-D black loop beneath live trajectories."""
        markers = self._field_markers
        required = ("A", "B", "C", "D")
        if any(name not in markers or len(markers[name]) != 2 for name in required):
            return
        radius_cm = float(self._competition_track.get("radius_cm", 0.0))
        if radius_cm <= 0.0:
            return
        a, b, c, d = (markers[name] for name in required)
        top_center = ((float(b[0]) + float(c[0])) / 2.0, float(b[1]))
        bottom_center = ((float(a[0]) + float(d[0])) / 2.0, float(a[1]))
        points = [(float(a[0]), float(a[1])), (float(b[0]), float(b[1]))]
        for index in range(1, 25):
            angle = math.pi - math.pi * index / 24.0
            points.append(
                (
                    top_center[0] + radius_cm * math.cos(angle),
                    top_center[1] + radius_cm * math.sin(angle),
                )
            )
        points.append((float(d[0]), float(d[1])))
        for index in range(1, 25):
            angle = -math.pi * index / 24.0
            points.append(
                (
                    bottom_center[0] + radius_cm * math.cos(angle),
                    bottom_center[1] + radius_cm * math.sin(angle),
                )
            )
        pen = QPen(QColor("#202020"), 4)
        for first, second in zip(points, points[1:]):
            start = self._scene_point(*first)
            end = self._scene_point(*second)
            self._scene.addLine(start.x(), start.y(), end.x(), end.y(), pen)

    def _draw_launch_point(self):
        point_value = self._launch_point
        if (
            isinstance(point_value, (str, bytes))
            or not hasattr(point_value, "__len__")
            or len(point_value) != 2
        ):
            return
        point = self._scene_point(point_value[0], point_value[1])
        color = QColor("#00897b")
        self._scene.addEllipse(
            point.x() - 15, point.y() - 15, 30, 30,
            QPen(color, 3), QBrush(QColor("#b2dfdb")),
        )
        self._scene.addEllipse(
            point.x() - 6, point.y() - 6, 12, 12,
            QPen(color, 2), QBrush(Qt.NoBrush),
        )
        self._scene.addText("H 起飞/降落点").setPos(point.x() + 18, point.y() - 10)

    def _draw_trajectory(self, points, color):
        if len(points) < 2:
            return
        pen = QPen(color, 2)
        for first, second in zip(points, points[1:]):
            start = self._scene_point(first.x_cm, first.y_cm)
            end = self._scene_point(second.x_cm, second.y_cm)
            self._scene.addLine(start.x(), start.y(), end.x(), end.y(), pen)

    def _draw_world_path(self, points, color):
        if len(points) < 2:
            return
        pen = QPen(color, 2, Qt.DashLine)
        for first, second in zip(points, points[1:]):
            start = self._scene_point(first[0], first[1])
            end = self._scene_point(second[0], second[1])
            self._scene.addLine(start.x(), start.y(), end.x(), end.y(), pen)

    def _draw_node(self, node, color, drone):
        pose = node.world_pose
        if not node.online or pose is None:
            return
        heading = math.radians(pose.heading_deg)
        alpha_color = QColor(color)
        if node.stale:
            alpha_color.setAlpha(110)
        reference_x_cm = pose.x_cm
        reference_y_cm = pose.y_cm
        if drone:
            forward = float(
                self._display_geometry.get(
                    "drone_reference_to_center_forward_cm", 0.0
                )
            )
            left = float(
                self._display_geometry.get("drone_reference_to_center_left_cm", 0.0)
            )
            radius = float(self._display_geometry.get("drone_radius_cm", 10.0))
            center_x_cm, center_y_cm = self._offset_point(
                reference_x_cm, reference_y_cm, heading, forward, left
            )
            center = self._scene_point(center_x_cm, center_y_cm)
            self._scene.addEllipse(
                center.x() - radius, center.y() - radius, radius * 2, radius * 2,
                QPen(color, 2), QBrush(alpha_color),
            )
            self._scene.addLine(
                center.x() - radius, center.y(), center.x() + radius, center.y(),
                QPen(Qt.black, 1),
            )
            self._scene.addLine(
                center.x(), center.y() - radius, center.x(), center.y() + radius,
                QPen(Qt.black, 1),
            )
            self._scene.addText("{:.0f} cm".format(pose.z_cm)).setPos(
                center.x() + radius + 2, center.y() - radius - 16
            )
        else:
            forward = float(
                self._display_geometry.get(
                    "car_reference_to_center_forward_cm", 7.125
                )
            )
            left = float(
                self._display_geometry.get(
                    "car_reference_to_center_left_cm", 0.0
                )
            )
            center_x_cm, center_y_cm = self._offset_point(
                reference_x_cm, reference_y_cm, heading, forward, left
            )
            length = float(self._display_geometry.get("car_body_length_cm", 23.0))
            width = float(self._display_geometry.get("car_body_width_cm", 14.5))
            body = self._rotated_rectangle(
                center_x_cm, center_y_cm, heading, length, width
            )
            self._scene.addPolygon(
                QPolygonF([self._scene_point(x_cm, y_cm) for x_cm, y_cm in body]),
                QPen(color, 2),
                QBrush(alpha_color),
            )
        arrow_start_x_cm, arrow_start_y_cm = reference_x_cm, reference_y_cm
        arrow_length = 30.0
        arrow_end_x_cm = arrow_start_x_cm + math.cos(heading) * arrow_length
        arrow_end_y_cm = arrow_start_y_cm + math.sin(heading) * arrow_length
        start = self._scene_point(arrow_start_x_cm, arrow_start_y_cm)
        end = self._scene_point(arrow_end_x_cm, arrow_end_y_cm)
        self._scene.addLine(start.x(), start.y(), end.x(), end.y(), QPen(Qt.black, 3))
        if not self._inside_field(reference_x_cm, reference_y_cm):
            self._scene.addEllipse(
                start.x() - 14, start.y() - 14, 28, 28,
                QPen(QColor("#e00000"), 3), QBrush(Qt.NoBrush),
            )
        if not node.node_flags & int(NodeFlags.POSE_VALID):
            self._scene.addLine(start.x() - 8, start.y() - 8, start.x() + 8, start.y() + 8, QPen(Qt.black, 2))
            self._scene.addLine(start.x() - 8, start.y() + 8, start.x() + 8, start.y() - 8, QPen(Qt.black, 2))

    @staticmethod
    def _offset_point(x_cm, y_cm, heading, forward_cm, left_cm):
        return (
            x_cm + math.cos(heading) * forward_cm - math.sin(heading) * left_cm,
            y_cm + math.sin(heading) * forward_cm + math.cos(heading) * left_cm,
        )

    @staticmethod
    def _rotated_rectangle(x_cm, y_cm, heading, length_cm, width_cm):
        points = ()
        for forward_cm, left_cm in (
            (length_cm / 2.0, width_cm / 2.0),
            (length_cm / 2.0, -width_cm / 2.0),
            (-length_cm / 2.0, -width_cm / 2.0),
            (-length_cm / 2.0, width_cm / 2.0),
        ):
            points += (
                FleetMapWidget._offset_point(
                    x_cm, y_cm, heading, forward_cm, left_cm
                ),
            )
        return points

    def _inside_field(self, x_cm, y_cm):
        return (
            0.0 <= x_cm <= self._field_width_cm
            and 0.0 <= y_cm <= self._field_height_cm
        )
