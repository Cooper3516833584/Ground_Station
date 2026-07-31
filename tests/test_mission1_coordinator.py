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
from components.mission1_coordinator import (
    CAR_MISSION2_REQUESTED,
    Mission1Coordinator,
    Mission1Timing,
    TASK_SPECS,
)


class FakeFuture:
    def __init__(self, result):
        self._result = result

    def result(self, _timeout=None):
        return self._result


class FakeMaster:
    def __init__(self, reject_command=None, accepted_command=None):
        self.commands = []
        self.last_seq = 0
        self.prepare_seq = 0
        self.reject_command = reject_command
        self.accepted_command = accepted_command

    def submit_command(self, node_id, command):
        self.last_seq += 1
        self.commands.append((int(node_id), int(command.command_id)))
        if command.command_id == CommandId.DRONE_PREPARE_MISSION:
            self.prepare_seq = self.last_seq
        request = Frame(
            1,
            NodeId.GROUND,
            node_id,
            MessageKind.COMMAND,
            0,
            10,
            self.last_seq,
            b"",
        )
        rejected = command.command_id == self.reject_command
        accepted = command.command_id == self.accepted_command
        response = Frame(
            1,
            node_id,
            NodeId.GROUND,
            MessageKind.ACK,
            0,
            20,
            self.last_seq,
            encode_ack(
                AckPayload(
                    10,
                    self.last_seq,
                    command.command_id,
                    (
                        AckStatus.REJECTED
                        if rejected
                        else (
                            AckStatus.ACCEPTED
                            if accepted
                            else AckStatus.COMPLETED
                        )
                    ),
                    AckReason.UNSUPPORTED if rejected else AckReason.NONE,
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


class FakeLed:
    def __init__(self):
        self.calls = []

    def solid(self, color, brightness=3):
        self.calls.append(("solid", tuple(color), brightness))

    def off(self):
        self.calls.append(("off",))


class Mission1CoordinatorTests(unittest.TestCase):
    def test_full_startup_sequence_orders_alerts_delays_and_commands(self):
        master = FakeMaster()
        led = FakeLed()
        buzzer_durations = []
        waits = []
        now = [0.0]

        def wait(duration):
            waits.append(duration)
            now[0] += duration
            return False

        def snapshot():
            selected = (
                int(NodeId.DRONE),
                int(CommandId.DRONE_SELECT_MISSION),
            ) in master.commands
            start_sent = (
                int(NodeId.DRONE),
                int(CommandId.DRONE_START_MISSION),
            ) in master.commands
            return SimpleNamespace(
                drone=SimpleNamespace(
                    online=True,
                    session=21 if selected else 20,
                    operation_state=(
                        4 if start_sent else (1 if selected else 30)
                    ),
                    active_command_seq=master.prepare_seq,
                    active_command_status=(
                        int(AckStatus.COMPLETED)
                        if master.prepare_seq
                        else 0
                    ),
                ),
                car=SimpleNamespace(
                    online=True,
                    session=99,
                    operation_state=13,
                    node_flags=int(NodeFlags.READY),
                ),
            )

        coordinator = Mission1Coordinator(
            master,
            snapshot,
            timing=Mission1Timing(),
            led_client=led,
            buzzer=buzzer_durations.append,
            wait=wait,
            monotonic=lambda: now[0],
        )
        coordinator.run_sequence()

        self.assertEqual(
            [
                (int(NodeId.DRONE), int(CommandId.DRONE_SELECT_MISSION)),
                (int(NodeId.DRONE), int(CommandId.DRONE_PREPARE_MISSION)),
                (int(NodeId.CAR), int(CommandId.CAR_ALARM_ON)),
                (int(NodeId.CAR), int(CommandId.CAR_ALARM_OFF)),
                (int(NodeId.DRONE), int(CommandId.DRONE_START_MISSION)),
                (int(NodeId.CAR), int(CommandId.CAR_START_MISSION)),
            ],
            master.commands,
        )
        self.assertEqual([0.2, 0.2, 0.2], buzzer_durations)
        self.assertIn(3.0, waits)
        self.assertIn(15.0, waits)
        self.assertIn(5.0, waits)
        self.assertEqual(3, len([call for call in led.calls if call[0] == "solid"]))
        self.assertTrue(
            all(call[1:] == ((255, 0, 0), 20) for call in led.calls if call[0] == "solid")
        )

    def test_rejected_select_continues_after_task_session_transition(self):
        master = FakeMaster(reject_command=CommandId.DRONE_SELECT_MISSION)

        def snapshot():
            selected = bool(master.commands)
            start_sent = (
                int(NodeId.DRONE),
                int(CommandId.DRONE_START_MISSION),
            ) in master.commands
            return SimpleNamespace(
                drone=SimpleNamespace(
                    online=True,
                    session=21 if selected else 20,
                    operation_state=4 if start_sent else (1 if selected else 30),
                    active_command_seq=master.prepare_seq,
                    active_command_status=(
                        int(AckStatus.COMPLETED) if master.prepare_seq else 0
                    ),
                ),
                car=SimpleNamespace(
                    online=True,
                    session=99,
                    operation_state=13,
                    node_flags=int(NodeFlags.READY),
                ),
            )

        coordinator = Mission1Coordinator(
            master,
            snapshot,
            led_client=FakeLed(),
            buzzer=lambda _duration: None,
            wait=lambda _duration: False,
        )
        coordinator.run_sequence()

        self.assertEqual(
            [
                (int(NodeId.DRONE), int(CommandId.DRONE_SELECT_MISSION)),
                (int(NodeId.DRONE), int(CommandId.DRONE_PREPARE_MISSION)),
                (int(NodeId.CAR), int(CommandId.CAR_ALARM_ON)),
                (int(NodeId.CAR), int(CommandId.CAR_ALARM_OFF)),
                (int(NodeId.DRONE), int(CommandId.DRONE_START_MISSION)),
                (int(NodeId.CAR), int(CommandId.CAR_START_MISSION)),
            ],
            master.commands,
        )

    def test_car_starts_immediately_after_drone_accepts_start(self):
        master = FakeMaster(accepted_command=CommandId.DRONE_START_MISSION)

        def snapshot():
            selected = (
                int(NodeId.DRONE),
                int(CommandId.DRONE_SELECT_MISSION),
            ) in master.commands
            if (
                int(NodeId.DRONE),
                int(CommandId.DRONE_START_MISSION),
            ) in master.commands:
                raise AssertionError(
                    "coordinator polled state after drone start acknowledgement"
                )
            return SimpleNamespace(
                drone=SimpleNamespace(
                    online=True,
                    session=21 if selected else 20,
                    operation_state=1 if selected else 30,
                    active_command_seq=master.prepare_seq,
                    active_command_status=(
                        int(AckStatus.COMPLETED)
                        if master.prepare_seq
                        else 0
                    ),
                ),
                car=SimpleNamespace(
                    online=True,
                    session=99,
                    operation_state=13,
                    node_flags=int(NodeFlags.READY),
                ),
            )

        coordinator = Mission1Coordinator(
            master,
            snapshot,
            led_client=FakeLed(),
            buzzer=lambda _duration: None,
            wait=lambda _duration: False,
        )
        coordinator.run_sequence()

        self.assertEqual(
            [
                (int(NodeId.DRONE), int(CommandId.DRONE_START_MISSION)),
                (int(NodeId.CAR), int(CommandId.CAR_START_MISSION)),
            ],
            master.commands[-2:],
        )

    def test_mission2_still_waits_for_drone_hover(self):
        master = FakeMaster(accepted_command=CommandId.DRONE_START_MISSION)
        post_start_polls = [0]

        def snapshot():
            selected = (
                int(NodeId.DRONE),
                int(CommandId.DRONE_SELECT_MISSION),
            ) in master.commands
            start_sent = (
                int(NodeId.DRONE),
                int(CommandId.DRONE_START_MISSION),
            ) in master.commands
            if start_sent:
                post_start_polls[0] += 1
            return SimpleNamespace(
                drone=SimpleNamespace(
                    online=True,
                    session=21 if selected else 20,
                    operation_state=(
                        4
                        if post_start_polls[0] >= 2
                        else (1 if selected else 30)
                    ),
                    active_command_seq=master.prepare_seq,
                    active_command_status=(
                        int(AckStatus.COMPLETED)
                        if master.prepare_seq
                        else 0
                    ),
                ),
                car=SimpleNamespace(
                    online=True,
                    session=99,
                    operation_state=14,
                    node_flags=int(NodeFlags.READY),
                ),
            )

        coordinator = Mission1Coordinator(
            master,
            snapshot,
            led_client=FakeLed(),
            buzzer=lambda _duration: None,
            wait=lambda _duration: False,
        )
        coordinator.run_sequence(TASK_SPECS[CAR_MISSION2_REQUESTED])

        self.assertGreaterEqual(post_start_polls[0], 2)
        self.assertEqual(
            (int(NodeId.CAR), int(CommandId.CAR_START_MISSION)),
            master.commands[-1],
        )

    def test_failure_after_selection_stops_drone_and_car(self):
        master = FakeMaster(reject_command=CommandId.DRONE_PREPARE_MISSION)

        def snapshot():
            selected = bool(master.commands)
            return SimpleNamespace(
                drone=SimpleNamespace(
                    online=True,
                    session=21 if selected else 20,
                    operation_state=1 if selected else 30,
                    active_command_seq=0,
                    active_command_status=0,
                ),
                car=SimpleNamespace(
                    online=True,
                    session=99,
                    operation_state=13,
                    node_flags=int(NodeFlags.READY),
                ),
            )

        coordinator = Mission1Coordinator(
            master,
            snapshot,
            led_client=FakeLed(),
            buzzer=lambda _duration: None,
            wait=lambda _duration: False,
        )
        with self.assertRaises(RuntimeError):
            coordinator.run_sequence()

        self.assertEqual(
            [
                (int(NodeId.DRONE), int(CommandId.DRONE_SELECT_MISSION)),
                (int(NodeId.DRONE), int(CommandId.DRONE_PREPARE_MISSION)),
                (int(NodeId.DRONE), int(CommandId.TARGETED_STOP)),
                (int(NodeId.CAR), int(CommandId.TARGETED_STOP)),
            ],
            master.commands,
        )


if __name__ == "__main__":
    unittest.main()
