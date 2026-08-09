import queue
import unittest
from types import SimpleNamespace

from components.fleet_models import (
    AckPayload,
    AckReason,
    AckStatus,
    CommandId,
    Frame,
    MessageKind,
    NodeFlags,
    NodeId,
)
from components.fleet_protocol import encode_ack
from components.road_patrol_coordinator import (
    RoadPatrolCoordinator,
    RoadPatrolTiming,
)
from components.road_patrol_status import RoadPatrolStage


def make_snapshot(
    drone_state,
    *,
    drone_session=10,
    drone_online=True,
    drone_stale=False,
    drone_missed_polls=0,
    car_ready=True,
    active_command_seq=0,
    active_command_status=0,
):
    return SimpleNamespace(
        drone=SimpleNamespace(
            online=drone_online,
            stale=drone_stale,
            missed_polls=drone_missed_polls,
            session=drone_session,
            operation_state=drone_state,
            active_command_seq=active_command_seq,
            active_command_status=active_command_status,
        ),
        car=SimpleNamespace(
            online=True,
            stale=False,
            node_flags=int(NodeFlags.READY) if car_ready else 0,
        ),
    )


class FakeFuture:
    def __init__(self, result):
        self._result = result

    def result(self, _timeout=None):
        return self._result


class FakeMaster:
    def __init__(self, events=None):
        self.commands = []
        self.events = events

    def submit_command(self, node_id, command):
        self.commands.append((int(node_id), int(command.command_id)))
        if self.events is not None:
            self.events.append(("command", int(node_id), int(command.command_id)))
        seq = len(self.commands)
        request = Frame(
            1,
            NodeId.GROUND,
            node_id,
            MessageKind.COMMAND,
            0,
            100,
            seq,
            b"",
        )
        response = Frame(
            1,
            node_id,
            NodeId.GROUND,
            MessageKind.ACK,
            0,
            200,
            seq,
            encode_ack(
                AckPayload(
                    100,
                    seq,
                    command.command_id,
                    AckStatus.COMPLETED,
                    AckReason.NONE,
                )
            ),
        )
        return FakeFuture(
            SimpleNamespace(
                succeeded=True,
                request=request,
                response=response,
                error="",
            )
        )


class SequencedFakeMaster(FakeMaster):
    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = list(outcomes)

    def submit_command(self, node_id, command):
        future = super().submit_command(node_id, command)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome:
            return future
        result = future.result()
        return FakeFuture(
            SimpleNamespace(
                succeeded=False,
                request=result.request,
                response=None,
                error="response timeout",
            )
        )


class FakeCuePlayer:
    def __init__(self, events=None):
        self.events = [] if events is None else events

    def play_stage_transition(self, **_kwargs):
        self.events.append(("cue", "triple"))

    def play_takeoff_warning(self, *, duration_seconds):
        self.events.append(("cue", "warning", duration_seconds))

    def turn_off(self):
        pass


class RoadPatrolCoordinatorTests(unittest.TestCase):
    def make_coordinator(self, master=None, snapshot=None):
        return RoadPatrolCoordinator(
            FakeMaster() if master is None else master,
            (lambda: make_snapshot(44)) if snapshot is None else snapshot,
            timing=RoadPatrolTiming(
                car_start_timeout_s=1.0,
                command_timeout_s=1.0,
                command_attempts=1,
                command_retry_interval_s=0.01,
            ),
            cue_player=FakeCuePlayer(),
            wait=lambda _duration: False,
        )

    def test_first_payload_released_report_queues_car_start_once(self):
        coordinator = self.make_coordinator()

        coordinator.observe_snapshot(make_snapshot(45, drone_session=20))
        self.assertEqual(20, coordinator._car_start_queue.get_nowait())
        self.assertIs(
            RoadPatrolStage.PAYLOAD_DELIVERY,
            coordinator._cue_queue.get_nowait(),
        )

        coordinator.observe_snapshot(make_snapshot(45, drone_session=20))
        coordinator.observe_snapshot(make_snapshot(45, drone_session=20))

        with self.assertRaises(queue.Empty):
            coordinator._car_start_queue.get_nowait()
        with self.assertRaises(queue.Empty):
            coordinator._cue_queue.get_nowait()

    def test_reconnect_after_payload_release_does_not_requeue_car_start(self):
        coordinator = self.make_coordinator()

        coordinator.observe_snapshot(make_snapshot(45, drone_session=20))
        self.assertEqual(20, coordinator._car_start_queue.get_nowait())
        coordinator.observe_snapshot(
            make_snapshot(
                45,
                drone_session=20,
                drone_online=False,
                drone_stale=True,
            )
        )
        coordinator.observe_snapshot(make_snapshot(45, drone_session=20))

        self.assertIs(RoadPatrolStage.PAYLOAD_DELIVERY, coordinator._cue_queue.get_nowait())
        with self.assertRaises(queue.Empty):
            coordinator._cue_queue.get_nowait()
        with self.assertRaises(queue.Empty):
            coordinator._car_start_queue.get_nowait()

    def test_payload_release_is_deduplicated_per_drone_session(self):
        coordinator = self.make_coordinator()

        coordinator.observe_snapshot(make_snapshot(45, drone_session=10))
        self.assertEqual(10, coordinator._car_start_queue.get_nowait())

        coordinator.observe_snapshot(make_snapshot(45, drone_session=11))
        self.assertEqual(11, coordinator._car_start_queue.get_nowait())

    def test_each_visible_transition_queues_one_cue(self):
        coordinator = self.make_coordinator()
        coordinator.observe_snapshot(make_snapshot(40))
        coordinator.observe_snapshot(make_snapshot(41))
        coordinator.observe_snapshot(make_snapshot(41))
        coordinator.observe_snapshot(make_snapshot(42))

        self.assertEqual(10, coordinator._takeoff_queue.get_nowait())
        self.assertIs(
            RoadPatrolStage.LINE_FOLLOWING,
            coordinator._cue_queue.get_nowait(),
        )
        with self.assertRaises(queue.Empty):
            coordinator._cue_queue.get_nowait()

    def test_physical_release_starts_car_once_without_duplicate_stage_cue(self):
        master = FakeMaster()
        current = [make_snapshot(44)]
        coordinator = self.make_coordinator(master, lambda: current[0])
        coordinator.observe_snapshot(current[0])
        self.assertIs(
            RoadPatrolStage.PAYLOAD_DELIVERY,
            coordinator._cue_queue.get_nowait(),
        )
        current[0] = make_snapshot(45, car_ready=False)
        coordinator.observe_snapshot(current[0])
        coordinator.observe_snapshot(current[0])

        with self.assertRaises(queue.Empty):
            coordinator._cue_queue.get_nowait()
        drone_session = coordinator._car_start_queue.get_nowait()
        self.assertEqual(10, drone_session)
        self.assertTrue(coordinator._dispatch_car_start(drone_session))
        self.assertEqual(
            [(int(NodeId.CAR), int(CommandId.CAR_START_MISSION))],
            master.commands,
        )
        with self.assertRaises(queue.Empty):
            coordinator._car_start_queue.get_nowait()

    def test_car_start_retries_without_reading_car_snapshot(self):
        now = [0.0]
        master = SequencedFakeMaster([False, RuntimeError("not ready"), True])
        coordinator = RoadPatrolCoordinator(
            master,
            lambda: make_snapshot(45, car_ready=False),
            timing=RoadPatrolTiming(
                car_start_timeout_s=1.0,
                command_attempts=1,
                command_retry_interval_s=0.1,
            ),
            cue_player=FakeCuePlayer(),
            wait=lambda duration: now.__setitem__(0, now[0] + duration),
            monotonic=lambda: now[0],
        )
        coordinator.observe_snapshot(make_snapshot(44))
        coordinator.observe_snapshot(make_snapshot(45, car_ready=False))
        self.assertTrue(coordinator._dispatch_car_start(10))
        self.assertEqual(3, len(master.commands))

    def test_car_start_has_total_timeout(self):
        now = [0.0]
        master = SequencedFakeMaster([False] * 10)
        coordinator = RoadPatrolCoordinator(
            master,
            lambda: make_snapshot(45, car_ready=False),
            timing=RoadPatrolTiming(
                car_start_timeout_s=0.2,
                command_attempts=1,
                command_retry_interval_s=0.1,
            ),
            cue_player=FakeCuePlayer(),
            wait=lambda duration: now.__setitem__(0, now[0] + duration),
            monotonic=lambda: now[0],
        )
        coordinator.observe_snapshot(make_snapshot(45))
        with self.assertRaises(TimeoutError):
            coordinator._dispatch_car_start(10)

    def test_new_aircraft_session_invalidates_a_queued_release(self):
        master = FakeMaster()
        current = [make_snapshot(44, drone_session=10)]
        coordinator = self.make_coordinator(master, lambda: current[0])
        coordinator.observe_snapshot(current[0])
        coordinator.observe_snapshot(make_snapshot(45, drone_session=10))
        queued_session = coordinator._car_start_queue.get_nowait()
        coordinator.observe_snapshot(make_snapshot(40, drone_session=11))

        self.assertFalse(coordinator._dispatch_car_start(queued_session))
        self.assertEqual([], master.commands)

    def test_ground_owns_prepare_15_second_warning_and_start_order(self):
        events = []
        now = [0.0]
        master = FakeMaster(events)

        def snapshot():
            command_sent = bool(master.commands)
            return make_snapshot(
                41,
                active_command_seq=len(master.commands) if command_sent else 0,
                active_command_status=(
                    int(AckStatus.COMPLETED) if command_sent else 0
                ),
            )

        def wait(duration):
            now[0] += duration
            return False

        coordinator = RoadPatrolCoordinator(
            master,
            snapshot,
            timing=RoadPatrolTiming(
                command_attempts=1,
                command_retry_interval_s=0.01,
            ),
            cue_player=FakeCuePlayer(events),
            wait=wait,
            monotonic=lambda: now[0],
        )
        coordinator.observe_snapshot(snapshot())
        session = coordinator._takeoff_queue.get_nowait()

        self.assertTrue(coordinator._run_takeoff_sequence(session))

        self.assertEqual(
            [
                ("cue", "triple"),
                ("command", int(NodeId.DRONE), int(CommandId.DRONE_PREPARE_MISSION)),
                ("cue", "warning", 5.0),
                ("command", int(NodeId.DRONE), int(CommandId.DRONE_START_MISSION)),
            ],
            events,
        )
        self.assertAlmostEqual(15.0, now[0])

    def test_landing_report_cancels_countdown_and_prevents_takeoff_command(self):
        events = []
        now = [0.0]
        operation_state = [41]
        master = FakeMaster(events)

        def snapshot():
            command_sent = bool(master.commands)
            return make_snapshot(
                operation_state[0],
                active_command_seq=len(master.commands) if command_sent else 0,
                active_command_status=(
                    int(AckStatus.COMPLETED) if command_sent else 0
                ),
            )

        def wait(duration):
            now[0] += duration
            operation_state[0] = 46
            return False

        coordinator = RoadPatrolCoordinator(
            master,
            snapshot,
            timing=RoadPatrolTiming(
                command_attempts=1,
                command_retry_interval_s=0.01,
            ),
            cue_player=FakeCuePlayer(events),
            wait=wait,
            monotonic=lambda: now[0],
        )
        coordinator.observe_snapshot(snapshot())
        session = coordinator._takeoff_queue.get_nowait()

        self.assertFalse(coordinator._run_takeoff_sequence(session))
        self.assertEqual(
            [
                (
                    int(NodeId.DRONE),
                    int(CommandId.DRONE_PREPARE_MISSION),
                )
            ],
            master.commands,
        )
        self.assertNotIn(("cue", "warning", 5.0), events)

        coordinator.observe_snapshot(snapshot())
        self.assertIs(
            RoadPatrolStage.LANDING,
            coordinator._cue_queue.get_nowait(),
        )

    def test_one_stale_poll_during_countdown_does_not_cancel_takeoff(self):
        now = [0.0]
        master = FakeMaster()
        post_prepare_polls = [0]

        def snapshot():
            command_sent = bool(master.commands)
            if command_sent:
                post_prepare_polls[0] += 1
            return make_snapshot(
                41,
                drone_stale=(post_prepare_polls[0] == 2),
                drone_missed_polls=(1 if post_prepare_polls[0] == 2 else 0),
                active_command_seq=len(master.commands) if command_sent else 0,
                active_command_status=(
                    int(AckStatus.COMPLETED) if command_sent else 0
                ),
            )

        def wait(duration):
            now[0] += duration
            return False

        coordinator = RoadPatrolCoordinator(
            master,
            snapshot,
            timing=RoadPatrolTiming(
                command_attempts=1,
                command_retry_interval_s=0.01,
            ),
            cue_player=FakeCuePlayer(),
            wait=wait,
            monotonic=lambda: now[0],
        )

        self.assertTrue(coordinator._run_takeoff_sequence(10))
        self.assertEqual(
            [
                (int(NodeId.DRONE), int(CommandId.DRONE_PREPARE_MISSION)),
                (int(NodeId.DRONE), int(CommandId.DRONE_START_MISSION)),
            ],
            master.commands,
        )

    def test_offline_cancellation_is_logged_and_same_session_can_retry(self):
        master = FakeMaster()
        current = [make_snapshot(41)]
        coordinator = self.make_coordinator(master, lambda: current[0])
        coordinator.observe_snapshot(current[0])
        session = coordinator._takeoff_queue.get_nowait()

        current[0] = make_snapshot(
            41,
            drone_online=False,
            drone_stale=True,
            drone_missed_polls=3,
        )
        with self.assertLogs("road-patrol-coordinator", level="WARNING") as logs:
            self.assertFalse(coordinator._run_takeoff_sequence(session))
        self.assertIn("before stage cue", logs.output[0])
        self.assertIn("after 3 missed polls", logs.output[0])

        coordinator._finish_takeoff_attempt(session, False)
        current[0] = make_snapshot(41)
        coordinator.observe_snapshot(current[0])
        self.assertEqual(session, coordinator._takeoff_queue.get_nowait())

    def test_successful_takeoff_session_is_not_queued_again(self):
        coordinator = self.make_coordinator()
        snapshot = make_snapshot(41)
        coordinator.observe_snapshot(snapshot)
        session = coordinator._takeoff_queue.get_nowait()
        coordinator._finish_takeoff_attempt(session, True)

        coordinator.observe_snapshot(snapshot)

        with self.assertRaises(queue.Empty):
            coordinator._takeoff_queue.get_nowait()

    def test_start_waits_past_accepted_until_completed(self):
        now = [0.0]
        master = FakeMaster()
        start_reports = [0]

        def snapshot():
            if len(master.commands) < 2:
                seq = len(master.commands)
                status = int(AckStatus.COMPLETED) if seq else 0
            else:
                seq = 2
                start_reports[0] += 1
                status = (
                    int(AckStatus.ACCEPTED)
                    if start_reports[0] == 1
                    else int(AckStatus.COMPLETED)
                )
            return make_snapshot(
                41, active_command_seq=seq, active_command_status=status
            )

        coordinator = RoadPatrolCoordinator(
            master,
            snapshot,
            timing=RoadPatrolTiming(command_attempts=1),
            cue_player=FakeCuePlayer(),
            wait=lambda duration: now.__setitem__(0, now[0] + duration),
            monotonic=lambda: now[0],
        )
        self.assertTrue(coordinator._run_takeoff_sequence(10))
        self.assertGreaterEqual(start_reports[0], 2)

    def test_start_failed_report_fails_takeoff_sequence(self):
        now = [0.0]
        master = FakeMaster()

        def snapshot():
            seq = len(master.commands)
            status = 0
            if seq == 1:
                status = int(AckStatus.COMPLETED)
            elif seq == 2:
                status = int(AckStatus.FAILED)
            return make_snapshot(
                41, active_command_seq=seq, active_command_status=status
            )

        coordinator = RoadPatrolCoordinator(
            master,
            snapshot,
            timing=RoadPatrolTiming(command_attempts=1),
            cue_player=FakeCuePlayer(),
            wait=lambda duration: now.__setitem__(0, now[0] + duration),
            monotonic=lambda: now[0],
        )
        with self.assertRaises(RuntimeError):
            coordinator._run_takeoff_sequence(10)


if __name__ == "__main__":
    unittest.main()
