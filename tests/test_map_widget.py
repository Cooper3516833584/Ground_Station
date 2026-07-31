"""Rendering regressions for the FleetBus map widget."""

import os
import sys
import unittest
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import QPointF, Qt
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import (
        QApplication,
        QGraphicsEllipseItem,
        QGraphicsPathItem,
        QLabel,
    )

    from components.fleet_models import NodeFlags
    from components.trajectory_store import TrajectoryPoint
    from components.ui.d_task_main_window import DTaskMainWindow, TrackingNodePanel
    from components.ui.map_widget import FleetMapWidget
except ImportError:
    FleetMapWidget = None


@unittest.skipIf(FleetMapWidget is None, "PyQt5 is not installed")
class FleetMapWidgetRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_overlapping_trajectory_dots_keep_an_opaque_center(self):
        widget = FleetMapWidget(field_width_cm=200, field_height_cm=200)
        point = TrajectoryPoint(
            timestamp=0.0,
            segment_id=0,
            x_cm=100.0,
            y_cm=100.0,
            z_cm=0.0,
            heading_deg=0.0,
            quality=2,
        )

        widget._draw_trajectory((point, point), QColor("#2979ff"))

        dot_items = [
            item
            for item in widget.scene().items()
            if isinstance(item, QGraphicsPathItem)
            and item.brush().style() != Qt.NoBrush
            and item.brush().color() == QColor("#2979ff")
        ]
        self.assertEqual(1, len(dot_items))
        dot_path = dot_items[0].path()
        self.assertEqual(Qt.WindingFill, dot_path.fillRule())
        self.assertTrue(dot_path.contains(QPointF(100.0, -100.0)))

    def test_snapshot_does_not_draw_drone_or_car_icons(self):
        widget = FleetMapWidget(field_width_cm=200, field_height_cm=200)
        node = SimpleNamespace(world_map_corners=(), world_path_points=())
        snapshot = SimpleNamespace(
            trajectories=(),
            drone=node,
            car=node,
        )

        widget.set_snapshot(snapshot)

        vehicle_colors = (QColor("#2979ff"), QColor("#ff6d00"))
        vehicle_items = [
            item
            for item in widget.scene().items()
            if hasattr(item, "brush")
            and item.brush().style() != Qt.NoBrush
            and item.brush().color() in vehicle_colors
        ]
        self.assertEqual([], vehicle_items)

    def test_launch_pad_geometry_and_boundary_clearance(self):
        widget = FleetMapWidget(
            field_width_cm=400,
            field_height_cm=500,
            launch_point=(112.5, 112.5),
        )

        widget._draw_field()

        ellipses = [
            item
            for item in widget.scene().items()
            if isinstance(item, QGraphicsEllipseItem)
        ]
        self.assertEqual(2, len(ellipses))
        by_width = {item.rect().width(): item.rect() for item in ellipses}
        outer = by_width[75.0]
        inner = by_width[50.0]
        self.assertEqual(QPointF(112.5, -112.5), outer.center())
        self.assertEqual(QPointF(112.5, -112.5), inner.center())
        self.assertAlmostEqual(75.0, outer.left())
        self.assertAlmostEqual(-75.0, outer.bottom())

    def test_stale_position_text_has_no_stale_suffix(self):
        panel = TrackingNodePanel("test", "drone")
        node = SimpleNamespace(
            online=True,
            stale=True,
            node_flags=int(NodeFlags.POSE_VALID),
            x_cm=10.0,
            y_cm=20.0,
            world_pose=SimpleNamespace(
                x_cm=30.0,
                y_cm=40.0,
                z_cm=50.0,
                heading_deg=60.0,
            ),
            battery_cV=1200,
            operation_state=0,
        )

        panel.update_snapshot(node)

        self.assertEqual("离线", panel._link.text())
        self.assertNotIn("陈旧", panel._local_position.text())
        self.assertNotIn("陈旧", panel._position.text())

        node.world_pose = None
        panel.update_snapshot(node)
        self.assertEqual("--", panel._position.text())

    def test_task_activity_controls_link_display_during_radio_silence(self):
        window = DTaskMainWindow()
        drone = SimpleNamespace(
            online=True,
            stale=False,
            session=10,
            node_flags=0,
            x_cm=0.0,
            y_cm=0.0,
            world_pose=None,
            battery_cV=0,
            operation_state=30,
            world_map_corners=(),
            world_path_points=(),
        )
        car = SimpleNamespace(
            online=True,
            stale=False,
            session=20,
            node_flags=0,
            x_cm=0.0,
            y_cm=0.0,
            world_pose=None,
            battery_cV=0,
            operation_state=2,
            world_map_corners=(),
            world_path_points=(),
        )
        snapshot = SimpleNamespace(trajectories=(), drone=drone, car=car)

        window.update_snapshot(snapshot)
        self.assertEqual("离线", window.drone_panel._link.text())
        self.assertEqual("离线", window.car_panel._link.text())

        drone.operation_state = 0
        window.update_snapshot(snapshot)
        self.assertEqual("在线", window.drone_panel._link.text())
        self.assertEqual("在线", window.car_panel._link.text())

        drone.online = False
        drone.stale = True
        car.online = False
        car.stale = True
        window.update_snapshot(snapshot)
        self.assertEqual("在线", window.drone_panel._link.text())
        self.assertEqual("在线", window.car_panel._link.text())
        self.assertIn("#16805b", window.drone_panel._link.styleSheet())
        self.assertIn("#16805b", window.car_panel._link.styleSheet())

        drone.operation_state = 11
        window.update_snapshot(snapshot)
        self.assertEqual("离线", window.drone_panel._link.text())
        self.assertEqual("离线", window.car_panel._link.text())
        window.close()

    def test_drone_mission_is_only_shown_in_large_bottom_label(self):
        window = DTaskMainWindow()
        drone = SimpleNamespace(
            online=True,
            stale=False,
            session=10,
            node_flags=int(NodeFlags.POSE_VALID),
            x_cm=10.0,
            y_cm=20.0,
            world_pose=SimpleNamespace(
                x_cm=30.0,
                y_cm=40.0,
                z_cm=50.0,
                heading_deg=60.0,
            ),
            battery_cV=1200,
            operation_state=5,
        )
        car = SimpleNamespace(
            online=True,
            stale=False,
            node_flags=int(NodeFlags.POSE_VALID),
            x_cm=15.0,
            y_cm=25.0,
            world_pose=SimpleNamespace(
                x_cm=35.0,
                y_cm=45.0,
                z_cm=0.0,
                heading_deg=90.0,
            ),
            battery_cV=1200,
            operation_state=4,
            world_map_corners=(),
            world_path_points=(),
        )
        snapshot = SimpleNamespace(
            trajectories=(),
            drone=drone,
            car=car,
        )

        window.resize(1000, 560)
        window.show()
        window.update_snapshot(snapshot)
        self.app.processEvents()

        self.assertFalse(hasattr(window.drone_panel, "_phase"))
        self.assertFalse(hasattr(window, "_relay"))
        self.assertFalse(any(
            "FleetBus" in label.text()
            for label in window.centralWidget().findChildren(QLabel)
        ))
        self.assertEqual("无人机伴飞", window._drone_mission.text())
        self.assertFalse(window._drone_mission.wordWrap())
        self.assertGreater(
            window._drone_mission.font().pointSize(),
            window.drone_panel._link.font().pointSize(),
        )
        for label in window.centralWidget().findChildren(QLabel):
            if not label.text():
                continue
            self.assertFalse(label.wordWrap(), label.text())
            self.assertGreaterEqual(
                label.width(), label.sizeHint().width(), label.text()
            )
        window.close()


if __name__ == "__main__":
    unittest.main()
