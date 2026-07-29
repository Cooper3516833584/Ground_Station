import math
import unittest

from components.coordinate_frames import CoordinateFrameRegistry, FrameTransform2D
from components.fleet_models import NodeId


class FrameTransform2DTests(unittest.TestCase):
    def test_points_vectors_and_headings_round_trip(self):
        frame = FrameTransform2D(100.0, -20.0, 90.0, revision=7)
        world_point = frame.local_to_world_point(10.0, 20.0)
        self.assertAlmostEqual(80.0, world_point[0])
        self.assertAlmostEqual(-10.0, world_point[1])
        local_point = frame.world_to_local_point(80.0, -10.0)
        self.assertAlmostEqual(10.0, local_point[0])
        self.assertAlmostEqual(20.0, local_point[1])
        vector = frame.local_to_world_vector(3.0, 4.0)
        self.assertTrue(math.isclose(-4.0, vector[0], abs_tol=1e-9))
        self.assertTrue(math.isclose(3.0, vector[1], abs_tol=1e-9))
        local_vector = frame.world_to_local_vector(*vector)
        self.assertAlmostEqual(3.0, local_vector[0])
        self.assertAlmostEqual(4.0, local_vector[1])
        self.assertEqual(0.0, frame.local_to_world_heading(270.0))
        self.assertEqual(270.0, frame.world_to_local_heading(0.0))

    def test_rejects_non_finite_numbers_and_invalid_revision(self):
        with self.assertRaises(ValueError):
            FrameTransform2D(float("nan"), 0, 0)
        with self.assertRaises(ValueError):
            FrameTransform2D(0, 0, 0, revision=0)
        frame = FrameTransform2D(0, 0, 0)
        with self.assertRaises(ValueError):
            frame.local_to_world_point(float("inf"), 0)
        with self.assertRaises(TypeError):
            frame.local_to_world_heading("90")

    def test_cardinal_and_arbitrary_angle_round_trips(self):
        for heading in (0.0, 90.0, 180.0, 270.0, 37.5):
            frame = FrameTransform2D(12.5, -8.75, heading)
            world = frame.local_to_world_point(31.25, -19.5)
            local = frame.world_to_local_point(*world)
            self.assertAlmostEqual(31.25, local[0], places=9)
            self.assertAlmostEqual(-19.5, local[1], places=9)
        frame = FrameTransform2D(0.0, 0.0, 1.0)
        self.assertAlmostEqual(0.99, frame.world_to_local_heading(1.99))
        self.assertAlmostEqual(0.99, frame.local_to_world_heading(359.99))


class CoordinateFrameRegistryTests(unittest.TestCase):
    def test_config_named_nodes_and_runtime_updates(self):
        registry = CoordinateFrameRegistry.from_config(
            {
                "coordinate_frames": {
                    "drone": {
                        "origin_world_cm": [10, 20],
                        "local_x_heading_world_deg": 30,
                        "revision": 4,
                    },
                    "car": {
                        "origin_world_x_cm": 1,
                        "origin_world_y_cm": 2,
                        "local_x_heading_world_deg": 3,
                    },
                }
            }
        )
        self.assertEqual(4, registry.revision(NodeId.DRONE))
        self.assertEqual(1, registry.revision(NodeId.CAR))
        registry.set(NodeId.CAR, FrameTransform2D(4, 5, 6, revision=2))
        self.assertEqual(2, registry.require(NodeId.CAR).revision)
        registry.remove(NodeId.CAR)
        self.assertIsNone(registry.get(NodeId.CAR))
        self.assertEqual(0, registry.revision(NodeId.CAR))
        with self.assertRaises(KeyError):
            registry.require(NodeId.CAR)


if __name__ == "__main__":
    unittest.main()
