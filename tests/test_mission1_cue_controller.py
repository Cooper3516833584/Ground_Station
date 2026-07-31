import unittest
from types import SimpleNamespace

from components.mission1_cue_controller import (
    CueKind,
    Mission1CueController,
    Mission1CueRun,
    Mission1CueTiming,
)


def snapshot(
    *,
    car_state,
    car_session=100,
    car_online=True,
    drone_state=30,
    drone_session=200,
    drone_online=True,
):
    return SimpleNamespace(
        car=SimpleNamespace(
            online=car_online,
            session=car_session,
            operation_state=car_state,
        ),
        drone=SimpleNamespace(
            online=drone_online,
            session=drone_session,
            operation_state=drone_state,
        ),
    )


class RecordingController(Mission1CueController):
    def __init__(self):
        super().__init__(lambda: None)
        self.submitted = []

    def _submit(self, run, cue_kind):
        self.submitted.append(cue_kind)


class Mission1CueControllerTests(unittest.TestCase):
    def test_only_mission1_request_activates_controller(self):
        controller = RecordingController()
        controller._observe(snapshot(car_state=14))
        controller._observe(
            snapshot(car_state=14, drone_state=5, drone_session=201)
        )

        self.assertIsNone(controller._run)
        self.assertEqual([], controller.submitted)

    def test_escort_requires_new_drone_task_session_and_is_deduplicated(self):
        controller = RecordingController()
        controller._observe(snapshot(car_state=13))
        controller._observe(snapshot(car_state=4, drone_state=5))
        self.assertEqual([], controller.submitted)

        controller._observe(
            snapshot(car_state=4, drone_state=5, drone_session=201)
        )
        controller._observe(
            snapshot(car_state=4, drone_state=5, drone_session=201)
        )

        self.assertEqual([CueKind.ESCORT_ACQUIRED], controller.submitted)

    def test_drop_uses_state_14_not_drop_started_state_6(self):
        controller = RecordingController()
        controller._observe(snapshot(car_state=13))
        controller._observe(
            snapshot(car_state=4, drone_state=6, drone_session=201)
        )
        self.assertEqual([], controller.submitted)

        controller._observe(
            snapshot(car_state=4, drone_state=14, drone_session=201)
        )
        controller._observe(
            snapshot(car_state=4, drone_state=14, drone_session=201)
        )

        self.assertEqual([CueKind.DROP], controller.submitted)

    def test_completion_requires_following_before_arrived(self):
        controller = RecordingController()
        controller._observe(snapshot(car_state=13))
        controller._observe(snapshot(car_state=7))
        self.assertEqual([], controller.submitted)

        controller = RecordingController()
        controller._observe(snapshot(car_state=13))
        controller._observe(snapshot(car_state=4))
        controller._observe(snapshot(car_state=7))
        controller._observe(snapshot(car_state=7))

        self.assertEqual([CueKind.COMPLETED], controller.submitted)

    def test_car_session_change_clears_active_run(self):
        controller = RecordingController()
        controller._observe(snapshot(car_state=13))
        controller._observe(snapshot(car_state=4, car_session=101))
        controller._observe(snapshot(car_state=7, car_session=101))

        self.assertIsNone(controller._run)
        self.assertEqual([], controller.submitted)

    def test_timing_config_requires_positive_values(self):
        timing = Mission1CueTiming.from_config(
            {"drop_duration_seconds": 1.25}
        )
        self.assertEqual(1.25, timing.drop_duration_s)
        with self.assertRaises(ValueError):
            Mission1CueTiming.from_config(
                {"monitor_interval_seconds": 0}
            )

    def test_worker_survives_one_cue_failure(self):
        calls = []

        class Player:
            def play_mission1_escort_acquired(self, **_kwargs):
                calls.append("escort")
                raise RuntimeError("cue failed")

            def play_mission1_drop(self, **_kwargs):
                calls.append("drop")

        controller = Mission1CueController(
            lambda: None,
            cue_player=Player(),
        )
        controller._queue.put(
            ((100, 201, CueKind.ESCORT_ACQUIRED), CueKind.ESCORT_ACQUIRED)
        )
        controller._queue.put(((100, 201, CueKind.DROP), CueKind.DROP))
        controller._queue.put(None)

        controller._worker_loop()

        self.assertEqual(["escort", "drop"], calls)
        self.assertTrue(controller._queue.empty())


if __name__ == "__main__":
    unittest.main()
