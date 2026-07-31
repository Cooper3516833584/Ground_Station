import unittest
from pathlib import Path

from components.trajectory_store import trajectory_policy_from_config
from land_air_app import load_config


ROOT = Path(__file__).resolve().parents[1]


class TrajectoryConfigTests(unittest.TestCase):
    def test_mission1_cue_timing_matches_required_waveforms(self):
        cues = load_config(ROOT / "d_task_fleet_config.json")["mission1_cues"]
        self.assertEqual(0.1, cues["monitor_interval_seconds"])
        self.assertEqual(0.2, cues["escort_on_seconds"])
        self.assertEqual(0.2, cues["escort_off_seconds"])
        self.assertEqual(1.0, cues["drop_duration_seconds"])
        self.assertEqual(1.0, cues["completion_duration_seconds"])

    def test_d_task_timing_uses_verified_values(self):
        config = load_config(ROOT / "d_task_fleet_config.json")
        timing = config["timing"]
        self.assertAlmostEqual(timing["node_turnaround_seconds"], 0.10)
        self.assertAlmostEqual(timing["response_timeout_seconds"], 0.75)
        self.assertAlmostEqual(timing["inter_slot_guard_seconds"], 0.05)

    def test_d_task_trace_sync_uses_bounded_backlog_catchup(self):
        trace_sync = load_config(ROOT / "d_task_fleet_config.json")["trace_sync"]
        self.assertEqual(trace_sync["max_samples_per_batch"], 15)
        self.assertEqual(trace_sync["max_catchup_batches"], 2)

    def test_d_task_node_policies_are_loaded(self):
        config = load_config(ROOT / "d_task_fleet_config.json")
        ui_config = config["ui"]
        drone = trajectory_policy_from_config(ui_config, "drone")
        car = trajectory_policy_from_config(ui_config, "car")
        self.assertAlmostEqual(drone.min_distance_cm, 1.0)
        self.assertAlmostEqual(drone.stationary_keepalive_s, 1.0)
        self.assertAlmostEqual(drone.max_speed_cm_s, 800.0)
        self.assertAlmostEqual(car.max_speed_cm_s, 300.0)
        self.assertEqual(1, drone.min_quality)
        self.assertEqual(1, car.min_quality)

    def test_drone_local_origin_matches_calibrated_launch_point(self):
        config = load_config(ROOT / "d_task_fleet_config.json")
        launch_point = config["field"]["launch_point"]
        drone_frame = config["coordinate_frames"]["drone"]

        self.assertEqual(launch_point, drone_frame["origin_world_cm"])
        self.assertEqual(3, drone_frame["revision"])

    def test_legacy_distance_field_remains_supported(self):
        policy = trajectory_policy_from_config(
            {"trajectory_min_distance_cm": 2.5},
            "drone",
        )
        self.assertAlmostEqual(policy.min_distance_cm, 2.5)
        self.assertAlmostEqual(policy.max_gap_s, 1.5)

    def test_new_distance_field_takes_precedence_over_legacy_value(self):
        policy = trajectory_policy_from_config(
            {
                "trajectory_min_distance_cm": -1,
                "trajectory": {"drone": {"min_distance_cm": 3.0}},
            },
            "drone",
        )
        self.assertAlmostEqual(policy.min_distance_cm, 3.0)

    def test_invalid_policy_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "max_gap_seconds"):
            trajectory_policy_from_config(
                {"trajectory": {"drone": {"max_gap_seconds": -1}}},
                "drone",
            )


if __name__ == "__main__":
    unittest.main()
