"""Rendering regressions for the FleetBus map widget."""

import os
import sys
import unittest
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import QPointF, Qt
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QApplication, QGraphicsPathItem

    from components.fleet_models import NodeFlags
    from components.trajectory_store import TrajectoryPoint
    from components.ui.d_task_main_window import TrackingNodePanel
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


if __name__ == "__main__":
    unittest.main()
