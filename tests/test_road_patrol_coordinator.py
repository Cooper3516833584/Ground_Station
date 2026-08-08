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
    car_ready=True,
    active_command_seq=0,
    active_command_status=0,
):
    return SimpleNamespace(
        drone=SimpleNamespace(
            online=drone_online,
            stale=drone_stale,
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
                car_ready_timeout_s=1.0,
                command_timeout_s=1.0,
                command_attempts=1,
                command_retry_interval_s=0.01,
            ),
            cue_player=FakeCuePlayer(),
            wait=lambda _duration: False,
        )

    def test_first_report_cues_visible_stage_but_reconnect_does_not_replay_car(self):
        coordinator = self.make_coordinator()

        coordinator.observe_snapshot(make_snapshot(45, drone_session=20))
        coordinator.observe_snapshot(
            make_snapshot(
                45,
                drone_session=20,
                drone_online=False,
                drone_stale=True,
            )
        )
        coordinator.observe_snapshot(make_snapshot(45, drone_session=20))

        self.assertIs(
            RoadPatrolStage.PAYLOAD_DELIVERY,
            coordinator._cue_queue.get_nowait(),
        )
        with self.assertRaises(queue.Empty):
            coordinator._cue_queue.get_nowait()
        with self.assertRaises(queue.Empty):
            coordinator._car_start_queue.get_nowait()

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
        current[0] = make_snapshot(45)
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
            prepare_sent = bool(
                master.commands
                and master.commands[-1]
                == (int(NodeId.DRONE), int(CommandId.DRONE_PREPARE_MISSION))
            )
            return make_snapshot(
                41,
                active_command_seq=1 if prepare_sent else 0,
                active_command_status=(
                    int(AckStatus.COMPLETED) if prepare_sent else 0
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
            prepare_sent = bool(master.commands)
            return make_snapshot(
                operation_state[0],
                active_command_seq=1 if prepare_sent else 0,
                active_command_status=(
                    int(AckStatus.COMPLETED) if prepare_sent else 0
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


if __name__ == "__main__":
    unittest.main()
