import unittest

from components.coordinate_frames import CoordinateFrameRegistry, FrameTransform2D
from components.fleet_models import CarNavigateCommand, NodeId
from components.fleet_protocol import decode_car_navigate, encode_car_navigate
from fleet_app import (
    field_target_to_local,
    ground_owned_coordinate_sync_enabled,
)


class FleetUiLogicTests(unittest.TestCase):
    def test_field_target_is_inverse_transformed_before_encoding(self):
        registry = CoordinateFrameRegistry()
        registry.set(NodeId.CAR, FrameTransform2D(100, 200, 90))
        local_x_cm, local_y_cm, local_heading_cdeg = field_target_to_local(
            registry, NodeId.CAR, 80, 230, 18000
        )
        self.assertEqual((30, 20, 9000), (local_x_cm, local_y_cm, local_heading_cdeg))
        command = decode_car_navigate(
            encode_car_navigate(
                CarNavigateCommand(local_x_cm, local_y_cm, local_heading_cdeg)
            )
        )
        self.assertEqual(
            CarNavigateCommand(30, 20, 9000),
            command,
        )

    def test_missing_frame_rejects_target_and_ground_owned_mode_disables_sync(self):
        with self.assertRaises(ValueError):
            field_target_to_local(
                CoordinateFrameRegistry(), NodeId.DRONE, 1, 2, None
            )
        self.assertFalse(ground_owned_coordinate_sync_enabled(True))
        self.assertTrue(ground_owned_coordinate_sync_enabled(False))

    def test_point_in_polygon_without_loading_qt(self):
        try:
            from components.ui.fleet_main_window import point_in_polygon
        except ImportError:
            self.skipTest("PyQt5 is not installed in this development environment")
        polygon = ((0, 0), (100, 0), (100, 100), (0, 100))
        self.assertTrue(point_in_polygon((50, 50), polygon))
        self.assertFalse(point_in_polygon((150, 50), polygon))


if __name__ == "__main__":
    unittest.main()
