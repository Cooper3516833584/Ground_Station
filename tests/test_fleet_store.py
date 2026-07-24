import tempfile
import unittest

from components.fleet_models import (
    AckPayload,
    Frame,
    MessageKind,
    NodeFlags,
    NodeId,
    ReportPayload,
)
from components.fleet_protocol import encode_ack, encode_report
from components.fleet_store import FleetStore
from components.trajectory_store import TrajectoryStore


def report_frame(session=1, x_cm=100, y_cm=200):
    return Frame(
        1,
        NodeId.DRONE,
        NodeId.GROUND,
        MessageKind.REPORT,
        0,
        session,
        1,
        encode_report(
            ReportPayload(
                5,
                6,
                int(NodeFlags.POSE_VALID | NodeFlags.READY),
                1000,
                x_cm,
                y_cm,
                300,
                9000,
                0,
                0,
                0,
                1234,
                2,
                4,
                0,
                0,
                0,
            )
        ),
    )


class FleetStoreTests(unittest.TestCase):
    def test_report_updates_node_and_trajectory(self):
        store = FleetStore()
        store.handle_frame(report_frame())
        snapshot = store.snapshot()
        self.assertTrue(snapshot.drone.online)
        self.assertFalse(snapshot.drone.stale)
        self.assertEqual((100, 200, 300), (
            snapshot.drone.x_cm, snapshot.drone.y_cm, snapshot.drone.z_cm
        ))
        self.assertEqual(1, len(dict(snapshot.trajectories)[NodeId.DRONE]))

    def test_timeouts_transition_offline(self):
        store = FleetStore(offline_after_missed_polls=3)
        store.handle_frame(report_frame())
        for _ in range(3):
            store.mark_timeout(NodeId.DRONE)
        self.assertFalse(store.snapshot().drone.online)

    def test_session_change_clears_old_ack(self):
        store = FleetStore()
        ack = Frame(
            1,
            NodeId.DRONE,
            NodeId.GROUND,
            MessageKind.ACK,
            0,
            10,
            1,
            encode_ack(AckPayload(2, 3, 1, 4)),
        )
        store.handle_frame(ack)
        self.assertIsNotNone(store.snapshot().drone.last_ack)
        store.handle_frame(report_frame(session=11))
        self.assertIsNone(store.snapshot().drone.last_ack)

    def test_vehicle_distance_does_not_change_state(self):
        store = FleetStore()
        store.handle_frame(report_frame(x_cm=50, y_cm=50))
        car = report_frame(x_cm=50, y_cm=50)
        car = Frame(
            car.version, NodeId.CAR, car.dst, car.kind, car.flags,
            car.session, car.seq, car.payload
        )
        store.handle_frame(car)
        snapshot = store.snapshot()
        self.assertTrue(snapshot.drone.online)
        self.assertTrue(snapshot.car.online)

    def test_pose_jump_only_records_warning(self):
        store = FleetStore(max_pose_jump_cm=100)
        store.handle_frame(report_frame(x_cm=0, y_cm=0))
        store.handle_frame(report_frame(x_cm=1000, y_cm=0))
        snapshot = store.snapshot()
        self.assertTrue(snapshot.drone.online)
        self.assertEqual(1000, snapshot.drone.x_cm)
        self.assertIn("pose jump", snapshot.drone.errors[-1])


class TrajectoryStoreTests(unittest.TestCase):
    def test_bounded_and_downsampled(self):
        store = TrajectoryStore((1,), max_points=2)
        self.assertTrue(store.append(1, 0, 0, timestamp=0))
        self.assertFalse(store.append(1, 0, 0, timestamp=0.5))
        self.assertTrue(store.append(1, 2, 0, timestamp=0.6))
        self.assertTrue(store.append(1, 4, 0, timestamp=0.7))
        self.assertEqual(2, len(store.snapshot()[1]))

    def test_export_csv(self):
        store = TrajectoryStore((1,))
        store.append(1, 10, 20, 30, timestamp=1.0)
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/trajectory.csv"
            self.assertEqual(1, store.export_csv(path))
            with open(path, encoding="utf-8") as handle:
                self.assertIn("1.0,1,10.0,20.0,30.0", handle.read())


if __name__ == "__main__":
    unittest.main()
