"""Fixed FIELD-frame QGraphicsView for FleetBus display."""

import math

from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt5.QtWidgets import QGraphicsScene, QGraphicsView

from components.fleet_models import NodeId
from components.trajectory_rendering import trajectory_segments


LAUNCH_PAD_OUTER_DIAMETER_CM = 75.0
LAUNCH_PAD_INNER_DIAMETER_CM = 50.0


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
        trajectory_minimum_quality=None,
    ):
        super().__init__(parent)
        self._field_width_cm = max(1.0, float(field_width_cm))
        self._field_height_cm = max(1.0, float(field_height_cm))
        self._display_geometry = display_geometry or {}
        self._field_markers = field_markers or {}
        self._competition_track = competition_track or {}
        self._launch_point = launch_point
        self._trajectory_minimum_quality = trajectory_minimum_quality or {}
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
            trajectories.get(int(NodeId.DRONE), ()),
            QColor("#2979ff"),
            self._trajectory_minimum_quality.get(int(NodeId.DRONE), 1),
        )
        self._draw_trajectory(
            trajectories.get(int(NodeId.CAR), ()),
            QColor("#ff6d00"),
            self._trajectory_minimum_quality.get(int(NodeId.CAR), 1),
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
        """Show the D-task coordinate directions outside the field boundary."""
        origin = QPointF(self._field_width_cm + 20.0, 20.0)
        axis_length = 24.0
        pen = QPen(QColor("#d32f2f"), 2)

        x_end = QPointF(origin.x(), origin.y() - axis_length)
        y_end = QPointF(origin.x() - axis_length, origin.y())
        self._scene.addLine(origin.x(), origin.y(), x_end.x(), x_end.y(), pen)
        self._scene.addLine(origin.x(), origin.y(), y_end.x(), y_end.y(), pen)
        self._scene.addLine(x_end.x(), x_end.y(), x_end.x() - 4, x_end.y() + 8, pen)
        self._scene.addLine(x_end.x(), x_end.y(), x_end.x() + 4, x_end.y() + 8, pen)
        self._scene.addLine(y_end.x(), y_end.y(), y_end.x() + 8, y_end.y() - 4, pen)
        self._scene.addLine(y_end.x(), y_end.y(), y_end.x() + 8, y_end.y() + 4, pen)
        x_label = self._scene.addText("X")
        x_label.setDefaultTextColor(QColor("#d32f2f"))
        x_label.setPos(x_end.x() + 5, x_end.y() - 14)
        y_label = self._scene.addText("Y")
        y_label.setDefaultTextColor(QColor("#d32f2f"))
        y_label.setPos(y_end.x() - 15, y_end.y() - 10)

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
        outer_radius = LAUNCH_PAD_OUTER_DIAMETER_CM / 2.0
        inner_radius = LAUNCH_PAD_INNER_DIAMETER_CM / 2.0
        self._scene.addEllipse(
            point.x() - outer_radius,
            point.y() - outer_radius,
            LAUNCH_PAD_OUTER_DIAMETER_CM,
            LAUNCH_PAD_OUTER_DIAMETER_CM,
            QPen(color, 3), QBrush(QColor("#b2dfdb")),
        )
        self._scene.addEllipse(
            point.x() - inner_radius,
            point.y() - inner_radius,
            LAUNCH_PAD_INNER_DIAMETER_CM,
            LAUNCH_PAD_INNER_DIAMETER_CM,
            QPen(color, 2), QBrush(Qt.NoBrush),
        )
        self._scene.addText("H 起飞/降落点").setPos(
            point.x() + outer_radius + 4.0,
            point.y() - 10.0,
        )

    def _draw_trajectory(self, points, color, minimum_quality=1):
        if not points:
            return
        pen = QPen(color, 2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        path = QPainterPath()
        for segment in trajectory_segments(points):
            first = self._scene_point(segment[0].x_cm, segment[0].y_cm)
            path.moveTo(first)
            for point_value in segment[1:]:
                path.lineTo(
                    self._scene_point(point_value.x_cm, point_value.y_cm)
                )
        self._scene.addPath(path, pen)

        normal_dots = QPainterPath()
        degraded_dots = QPainterPath()
        # A trajectory can legitimately contain repeated or near-identical
        # samples.  QPainterPath defaults to OddEvenFill, which makes
        # overlapping ellipses cancel each other and leaves transparent holes.
        # WindingFill keeps the union of all sampled dots solid.
        normal_dots.setFillRule(Qt.WindingFill)
        degraded_dots.setFillRule(Qt.WindingFill)
        dot_radius = 4.0
        for point_value in points:
            point = self._scene_point(point_value.x_cm, point_value.y_cm)
            dot_path = (
                normal_dots
                if point_value.quality >= minimum_quality
                else degraded_dots
            )
            dot_path.addEllipse(
                point.x() - dot_radius,
                point.y() - dot_radius,
                dot_radius * 2.0,
                dot_radius * 2.0,
            )
        self._scene.addPath(
            normal_dots, QPen(Qt.NoPen), QBrush(color)
        )
        self._scene.addPath(
            degraded_dots, QPen(color, 1), QBrush(Qt.NoBrush)
        )

    def _draw_world_path(self, points, color):
        if len(points) < 2:
            return
        pen = QPen(color, 2, Qt.DashLine)
        for first, second in zip(points, points[1:]):
            start = self._scene_point(first[0], first[1])
            end = self._scene_point(second[0], second[1])
            self._scene.addLine(start.x(), start.y(), end.x(), end.y(), pen)
