import unittest
from types import SimpleNamespace

from components.mission2_cue_controller import (
    CueKind,
    Mission2CueController,
    Mission2CueTiming,
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


class RecordingController(Mission2CueController):
    def __init__(self):
        super().__init__(lambda: None)
        self.submitted = []

    def _submit(self, run, cue_kind):
        self.submitted.append(cue_kind)


def start_run(controller):
    controller._observe(snapshot(car_state=14))
    controller._observe(
        snapshot(car_state=14, drone_state=1, drone_session=201)
    )


class Mission2CueControllerTests(unittest.TestCase):
    def test_mission2_request_creates_run_and_new_session_is_bound(self):
        controller = RecordingController()

        start_run(controller)

        self.assertIsNotNone(controller._run)
        self.assertEqual(100, controller._run.car_session)
        self.assertEqual(201, controller._run.drone_task_session)

    def test_old_drone_session_states_do_not_trigger(self):
        controller = RecordingController()
        controller._observe(snapshot(car_state=14))

        for state in (5, 8, 3, 11):
            controller._observe(
                snapshot(car_state=14, drone_state=state, drone_session=200)
            )

        self.assertEqual([], controller.submitted)

    def test_escort_and_target_locked_are_each_deduplicated(self):
        controller = RecordingController()
        start_run(controller)

        controller._observe(
            snapshot(car_state=4, drone_state=5, drone_session=201)
        )
        controller._observe(
            snapshot(car_state=4, drone_state=5, drone_session=201)
        )
        controller._observe(
            snapshot(car_state=4, drone_state=7, drone_session=201)
        )
        controller._observe(
            snapshot(car_state=4, drone_state=8, drone_session=201)
        )
        controller._observe(
            snapshot(car_state=4, drone_state=8, drone_session=201)
        )

        self.assertEqual(
            [CueKind.ESCORT_ACQUIRED, CueKind.TARGET_LOCKED],
            controller.submitted,
        )

    def test_retakeoff_requires_prior_target_lock(self):
        controller = RecordingController()
        start_run(controller)

        controller._observe(
            snapshot(car_state=4, drone_state=3, drone_session=201)
        )
        self.assertEqual([], controller.submitted)

        controller._observe(
            snapshot(car_state=4, drone_state=8, drone_session=201)
        )
        controller._observe(
            snapshot(car_state=4, drone_state=3, drone_session=201)
        )
        controller._observe(
            snapshot(car_state=4, drone_state=3, drone_session=201)
        )

        self.assertEqual(
            [CueKind.TARGET_LOCKED, CueKind.RETAKEOFF_STARTED],
            controller.submitted,
        )

    def test_car_first_completion_waits_for_drone(self):
        controller = RecordingController()
        start_run(controller)
        controller._observe(
            snapshot(car_state=4, drone_state=9, drone_session=201)
        )
        controller._observe(
            snapshot(car_state=7, drone_state=9, drone_session=201)
        )
        self.assertNotIn(CueKind.COMPLETED, controller.submitted)

        controller._observe(
            snapshot(car_state=7, drone_state=11, drone_session=201)
        )

        self.assertEqual(1, controller.submitted.count(CueKind.COMPLETED))
        self.assertIsNone(controller._run)

    def test_drone_first_completion_waits_for_car(self):
        controller = RecordingController()
        start_run(controller)
        controller._observe(
            snapshot(car_state=4, drone_state=11, drone_session=201)
        )
        self.assertNotIn(CueKind.COMPLETED, controller.submitted)

        controller._observe(
            snapshot(car_state=7, drone_state=30, drone_session=202)
        )

        self.assertEqual(1, controller.submitted.count(CueKind.COMPLETED))

    def test_car_arrival_survives_offline_until_drone_completes(self):
        controller = RecordingController()
        start_run(controller)
        controller._observe(
            snapshot(car_state=4, drone_state=9, drone_session=201)
        )
        controller._observe(
            snapshot(car_state=7, drone_state=9, drone_session=201)
        )
        controller._observe(
            snapshot(
                car_state=7,
                car_online=False,
                drone_state=11,
                drone_session=201,
            )
        )

        self.assertEqual(1, controller.submitted.count(CueKind.COMPLETED))

    def test_arrived_without_following_is_not_completion(self):
        controller = RecordingController()
        start_run(controller)
        controller._observe(
            snapshot(car_state=7, drone_state=11, drone_session=201)
        )

        self.assertNotIn(CueKind.COMPLETED, controller.submitted)
        self.assertFalse(controller._run.car_arrived)

    def test_fault_states_end_run_without_completion(self):
        for car_state in (11, 12):
            controller = RecordingController()
            start_run(controller)
            controller._observe(
                snapshot(car_state=car_state, drone_state=9, drone_session=201)
            )
            self.assertIsNone(controller._run)
            self.assertNotIn(CueKind.COMPLETED, controller.submitted)

        for drone_state in (12, 13):
            controller = RecordingController()
            start_run(controller)
            controller._observe(
                snapshot(car_state=4, drone_state=drone_state, drone_session=201)
            )
            self.assertIsNone(controller._run)
            self.assertNotIn(CueKind.COMPLETED, controller.submitted)

    def test_new_car_session_does_not_inherit_old_events(self):
        controller = RecordingController()
        start_run(controller)
        controller._observe(
            snapshot(car_state=4, drone_state=8, drone_session=201)
        )

        controller._observe(
            snapshot(car_state=14, car_session=101, drone_session=201)
        )

        self.assertEqual(101, controller._run.car_session)
        self.assertFalse(controller._run.target_locked_seen)
        self.assertFalse(controller._run.car_arrived)
        self.assertFalse(controller._run.drone_completed)

    def test_timing_config_and_positive_validation(self):
        timing = Mission2CueTiming.from_config(
            {
                "monitor_interval_seconds": 0.15,
                "escort_on_seconds": 0.25,
                "escort_off_seconds": 0.35,
                "target_locked_duration_seconds": 1.1,
                "retakeoff_duration_seconds": 1.2,
                "completion_duration_seconds": 1.3,
            }
        )
        self.assertEqual(
            (0.15, 0.25, 0.35, 1.1, 1.2, 1.3),
            (
                timing.monitor_interval_s,
                timing.escort_on_s,
                timing.escort_off_s,
                timing.target_locked_duration_s,
                timing.retakeoff_duration_s,
                timing.completion_duration_s,
            ),
        )
        for key in (
            "monitor_interval_seconds",
            "escort_on_seconds",
            "escort_off_seconds",
            "target_locked_duration_seconds",
            "retakeoff_duration_seconds",
            "completion_duration_seconds",
        ):
            with self.assertRaises(ValueError):
                Mission2CueTiming.from_config({key: 0})

    def test_worker_survives_one_cue_failure(self):
        calls = []

        class Player:
            def play_mission2_target_locked(self, **_kwargs):
                calls.append("locked")
                raise RuntimeError("cue failed")

            def play_mission2_retakeoff_started(self, **_kwargs):
                calls.append("retakeoff")

        controller = Mission2CueController(
            lambda: None,
            cue_player=Player(),
        )
        controller._queue.put(
            ((100, 201, CueKind.TARGET_LOCKED), CueKind.TARGET_LOCKED)
        )
        controller._queue.put(
            (
                (100, 201, CueKind.RETAKEOFF_STARTED),
                CueKind.RETAKEOFF_STARTED,
            )
        )
        controller._queue.put(None)

        controller._worker_loop()

        self.assertEqual(["locked", "retakeoff"], calls)
        self.assertTrue(controller._queue.empty())


if __name__ == "__main__":
    unittest.main()
