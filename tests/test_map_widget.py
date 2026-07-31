"""Rendering regressions for the FleetBus map widget."""

import os
import sys
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import QPointF, Qt
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QApplication, QGraphicsPathItem

    from components.trajectory_store import TrajectoryPoint
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


if __name__ == "__main__":
    unittest.main()
