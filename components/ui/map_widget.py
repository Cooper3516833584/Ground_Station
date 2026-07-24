"""QGraphicsView map for the shared FleetBus world coordinate frame."""

import math

from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QGraphicsScene, QGraphicsView

from components.fleet_models import NodeId


class FleetMapWidget(QGraphicsView):
    target_clicked = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setMinimumSize(640, 480)
        self._snapshot = None
        self._targets = {}

    def set_snapshot(self, snapshot):
        self._snapshot = snapshot
        self._redraw()

    def set_target(self, node_id, x_cm, y_cm):
        self._targets[int(node_id)] = (x_cm, y_cm)
        self._redraw()

    def mouseDoubleClickEvent(self, event):
        point = self.mapToScene(event.pos())
        self.target_clicked.emit(point.x(), -point.y())
        super().mouseDoubleClickEvent(event)

    def _redraw(self):
        self._scene.clear()
        axis = QPen(QColor("#777777"), 0)
        self._scene.addLine(-10000, 0, 10000, 0, axis)
        self._scene.addLine(0, -10000, 0, 10000, axis)
        self._scene.addLine(0, 40, 100, 40, QPen(Qt.black, 3))
        self._scene.addText("100 cm").setPos(0, 42)
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
        if car.map_corners:
            polygon = QPolygonF(
                [QPointF(x_cm, -y_cm) for x_cm, y_cm in car.map_corners]
            )
            self._scene.addPolygon(
                polygon, QPen(QColor("#2e7d32"), 2), QBrush(Qt.NoBrush)
            )
        self._draw_node(self._snapshot.drone, QColor("#2979ff"), drone=True)
        self._draw_node(self._snapshot.car, QColor("#ff6d00"), drone=False)
        for node_id, (x_cm, y_cm) in self._targets.items():
            color = QColor("#2979ff" if node_id == NodeId.DRONE else "#ff6d00")
            self._scene.addEllipse(
                x_cm - 6, -y_cm - 6, 12, 12, QPen(color, 2), QBrush(Qt.NoBrush)
            )
        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-100, -100, 100, 100))

    def _draw_trajectory(self, points, color):
        if len(points) < 2:
            return
        pen = QPen(color, 2)
        for first, second in zip(points, points[1:]):
            self._scene.addLine(
                first.x_cm,
                -first.y_cm,
                second.x_cm,
                -second.y_cm,
                pen,
            )

    def _draw_node(self, node, color, drone):
        if not node.online:
            return
        x_cm, y_cm = node.x_cm, -node.y_cm
        heading = math.radians(node.heading_cdeg / 100.0)
        if drone:
            self._scene.addEllipse(
                x_cm - 10, y_cm - 10, 20, 20, QPen(color, 2), QBrush(color)
            )
        else:
            self._scene.addRect(
                x_cm - 12, y_cm - 8, 24, 16, QPen(color, 2), QBrush(color)
            )
        self._scene.addLine(
            x_cm,
            y_cm,
            x_cm + math.cos(heading) * 30,
            y_cm - math.sin(heading) * 30,
            QPen(Qt.black, 3),
        )
