import tempfile
import unittest

from components.fleet_models import (
    AckPayload,
    Frame,
    MapReportPayload,
    MessageKind,
    NodeFlags,
    NodeId,
    ReportPayload,
    PathReportPayload,
    SurveyFlags,
    SurveyReportPayload,
    TerrainCode,
)
from components.fleet_protocol import (
    encode_ack,
    encode_map_report,
    encode_path_report,
    encode_report,
    encode_survey_report,
)
from components.coordinate_frames import FrameTransform2D
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
    def assertPointsAlmostEqual(self, expected, actual):
        self.assertEqual(len(expected), len(actual))
        for expected_point, actual_point in zip(expected, actual):
            self.assertAlmostEqual(expected_point[0], actual_point[0])
            self.assertAlmostEqual(expected_point[1], actual_point[1])

    def test_report_updates_node_and_trajectory(self):
        store = FleetStore()
        store.handle_frame(report_frame())
        snapshot = store.snapshot()
        self.assertTrue(snapshot.drone.online)
        self.assertFalse(snapshot.drone.stale)
        self.assertEqual((100, 200, 300), (
            snapshot.drone.x_cm, snapshot.drone.y_cm, snapshot.drone.z_cm
        ))
        self.assertIsNone(snapshot.drone.world_pose)
        self.assertFalse(snapshot.drone.frame_valid)
        self.assertEqual(0, len(dict(snapshot.trajectories)[NodeId.DRONE]))

    def test_report_derives_field_pose_and_trajectory_from_fixed_frame(self):
        store = FleetStore()
        store.set_frame_transform(
            NodeId.DRONE, FrameTransform2D(1000, -50, 90, revision=3)
        )
        store.handle_frame(report_frame())
        snapshot = store.snapshot().drone
        self.assertEqual((100, 200), (snapshot.x_cm, snapshot.y_cm))
        self.assertTrue(snapshot.frame_valid)
        self.assertEqual(3, snapshot.frame_revision)
        self.assertPointsAlmostEqual(
            ((800.0, 50.0),),
            ((snapshot.world_pose.x_cm, snapshot.world_pose.y_cm),),
        )
        self.assertEqual(180.0, snapshot.world_pose.heading_deg)
        point = dict(store.snapshot().trajectories)[NodeId.DRONE][0]
        self.assertPointsAlmostEqual(
            ((800.0, 50.0),), ((point.x_cm, point.y_cm),)
        )

    def test_map_and_path_keep_local_values_and_derive_field_values(self):
        store = FleetStore()
        store.set_frame_transform(
            NodeId.CAR, FrameTransform2D(1000, -50, 90, revision=2)
        )
        map_frame = Frame(
            1, NodeId.CAR, NodeId.GROUND, MessageKind.MAP_REPORT, 0, 1, 1,
            encode_map_report(
                MapReportPayload(1, 1, 1, ((0, 0), (100, 0), (100, 50), (0, 50)))
            ),
        )
        path_frame = Frame(
            1, NodeId.CAR, NodeId.GROUND, MessageKind.PATH_REPORT, 0, 1, 2,
            encode_path_report(
                PathReportPayload(1, 2, 1, ((0, 0), (25, 10)))
            ),
        )
        store.handle_frame(map_frame)
        store.handle_frame(path_frame)
        car = store.snapshot().car
        self.assertEqual(((0, 0), (100, 0), (100, 50), (0, 50)), car.map_corners)
        self.assertPointsAlmostEqual(
            ((1000.0, -50.0), (1000.0, 50.0), (950.0, 50.0), (950.0, -50.0)),
            car.world_map_corners,
        )
        self.assertEqual(((0, 0), (25, 10)), car.path_points)
        self.assertPointsAlmostEqual(
            ((1000.0, -50.0), (990.0, -25.0)), car.world_path_points
        )

    def test_runtime_frame_update_rebuilds_field_data_and_clears_trajectory(self):
        store = FleetStore()
        store.set_frame_transform(NodeId.DRONE, FrameTransform2D(0, 0, 0))
        store.handle_frame(report_frame())
        self.assertEqual(1, len(dict(store.snapshot().trajectories)[NodeId.DRONE]))

        store.set_frame_transform(
            NodeId.DRONE, FrameTransform2D(10, 20, 180, revision=2)
        )
        drone = store.snapshot().drone
        self.assertEqual(1, drone.session)
        self.assertEqual((100, 200), (drone.x_cm, drone.y_cm))
        self.assertEqual(2, drone.frame_revision)
        self.assertPointsAlmostEqual(
            ((-90.0, -180.0),),
            ((drone.world_pose.x_cm, drone.world_pose.y_cm),),
        )
        self.assertEqual(0, len(dict(store.snapshot().trajectories)[NodeId.DRONE]))

    def test_session_change_keeps_fixed_frame_and_clears_trajectory(self):
        store = FleetStore()
        store.set_frame_transform(NodeId.DRONE, FrameTransform2D(10, 20, 0))
        store.handle_frame(report_frame(session=1))
        store.handle_frame(report_frame(session=2, x_cm=1, y_cm=2))
        drone = store.snapshot().drone
        self.assertEqual(2, drone.session)
        self.assertTrue(drone.frame_valid)
        self.assertPointsAlmostEqual(
            ((11.0, 22.0),),
            ((drone.world_pose.x_cm, drone.world_pose.y_cm),),
        )
        self.assertEqual(1, len(dict(store.snapshot().trajectories)[NodeId.DRONE]))

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

    def test_survey_report_updates_grid_and_disaster_banner_state(self):
        terrain = (int(TerrainCode.FIELD),) * 14 + (int(TerrainCode.WILDFIRE),)
        positions = tuple((115 + 70 * col, 175 + 70 * row) for row in range(3) for col in range(5))
        frame = Frame(
            1, NodeId.DRONE, NodeId.GROUND, MessageKind.SURVEY_REPORT,
            0, 12, 2,
            encode_survey_report(
                SurveyReportPayload(
                    5, 6, 9,
                    int(SurveyFlags.COMPLETE | SurveyFlags.ABSOLUTE_POSITIONS),
                    3, 2, 4, 0, 0xFF, 0xFF, terrain, positions,
                )
            ),
        )
        store = FleetStore()
        store.handle_frame(frame)
        snapshot = store.snapshot().drone
        self.assertEqual(9, snapshot.survey_revision)
        self.assertEqual(3, snapshot.wildfire_event_id)
        self.assertEqual(int(TerrainCode.WILDFIRE), snapshot.terrain_codes[-1])
        self.assertEqual((395, 315), snapshot.survey_cell_positions_cm[-1])


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
