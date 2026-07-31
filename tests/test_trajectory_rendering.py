import unittest

from components.trajectory_store import TrajectoryPoint
from components.trajectory_rendering import trajectory_segments


def point(segment_id, x_cm):
    return TrajectoryPoint(
        timestamp=float(x_cm),
        segment_id=segment_id,
        x_cm=float(x_cm),
        y_cm=0.0,
        z_cm=0.0,
        heading_deg=0.0,
        quality=2,
    )


class TrajectoryRenderingTests(unittest.TestCase):
    def test_same_segment_is_grouped(self):
        points = (point(0, 0), point(0, 1))
        self.assertEqual((points,), trajectory_segments(points))

    def test_different_segments_are_not_connected(self):
        points = (point(0, 0), point(1, 1), point(1, 2))
        self.assertEqual(
            ((points[0],), (points[1], points[2])),
            trajectory_segments(points),
        )

    def test_singleton_segment_is_retained(self):
        singleton = point(3, 0)
        self.assertEqual(((singleton,),), trajectory_segments((singleton,)))

    def test_empty_input_returns_empty_result(self):
        self.assertEqual((), trajectory_segments(()))


if __name__ == "__main__":
    unittest.main()
