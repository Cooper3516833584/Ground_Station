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
    Mission1Coordinator,
    Mission1Timing,
)


class FakeFuture:
    def __init__(self, result):
        self._result = result

    def result(self, _timeout=None):
        return self._result


class FakeMaster:
    def __init__(self):
        self.commands = []
        self.last_seq = 0
        self.prepare_seq = 0

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
            start_sent = (
                int(NodeId.DRONE),
                int(CommandId.DRONE_START_MISSION),
            ) in master.commands
            return SimpleNamespace(
                drone=SimpleNamespace(
                    online=True,
                    operation_state=4 if start_sent else 1,
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


if __name__ == "__main__":
    unittest.main()
