import unittest

from command_service import (
    CommandService,
    CommandValidator,
    RecentCommandCache,
)
from models import AckStatus, Command, CommandAck, CommandId, MessageType
from protocol import Frame, pack_frame


KEY = bytes.fromhex("00112233445566778899aabbccddeeff")


class FakeWriter:
    def __init__(self):
        self.writes = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class RetryTests(unittest.TestCase):
    def test_retries_keep_same_frame_and_seq(self):
        writer = FakeWriter()
        clock = FakeClock()
        service = CommandService(
            writer=writer,
            key=KEY,
            session=10,
            now=clock,
            timeout_seconds=0.8,
            max_retries=3,
        )
        seq = service.send(Command(CommandId.PING))
        self.assertEqual(seq, 1)
        self.assertEqual(len(writer.writes), 1)

        for step in range(3):
            clock.value += 0.8
            service.poll()
            self.assertEqual(len(writer.writes), step + 2)
            self.assertEqual(writer.writes[-1], writer.writes[0])

        clock.value += 0.8
        service.poll()
        self.assertTrue(service.pending.done)
        self.assertEqual(service.pending.failed_reason, "ack timeout")
        self.assertEqual(len(writer.writes), 4)

    def test_accepted_is_not_completed(self):
        writer = FakeWriter()
        clock = FakeClock()
        service = CommandService(writer=writer, key=KEY, session=10, now=clock)
        seq = service.send(Command(CommandId.PING))
        accepted = CommandAck(
            MessageType.COMMAND,
            CommandId.PING,
            seq,
            AckStatus.ACCEPTED,
        )
        frame = Frame(1, MessageType.COMMAND_ACK, 0, 10, 99, accepted.to_payload())
        service.on_ack(frame)
        self.assertFalse(service.pending.done)

        completed = CommandAck(
            MessageType.COMMAND,
            CommandId.PING,
            seq,
            AckStatus.COMPLETED,
        )
        result = Frame(1, MessageType.COMMAND_RESULT, 0, 10, 100, completed.to_payload())
        service.on_ack(result)
        self.assertTrue(service.pending.done)

    def test_duplicate_command_seq_reuses_cached_response(self):
        cache = RecentCommandCache(max_items=64)
        validator = CommandValidator()
        executions = 0

        def handle(session, seq, command):
            nonlocal executions
            cached = cache.get(session, seq)
            if cached is not None:
                return cached
            reason = validator.validate(command)
            status = AckStatus.ACCEPTED if reason.name == "NONE" else AckStatus.REJECTED
            if status == AckStatus.ACCEPTED and validator.apply_accepted(command):
                executions += 1
            payload = CommandAck(
                MessageType.COMMAND,
                command.command_id,
                seq,
                status,
                reason,
            ).to_payload()
            cache.put(session, seq, payload)
            return payload

        command = Command(CommandId.SET_TARGETS, 1, 2)
        first = handle(33, 7, command)
        second = handle(33, 7, command)
        self.assertEqual(first, second)
        self.assertEqual(executions, 1)

    def test_stop_with_different_seq_is_idempotent(self):
        validator = CommandValidator()
        executions = 0
        for _seq in (1, 2, 3):
            command = Command(CommandId.STOP_MISSION)
            self.assertEqual(validator.validate(command).name, "NONE")
            if validator.apply_accepted(command):
                executions += 1
        self.assertEqual(executions, 1)

    def test_one_hundred_commands_can_receive_terminal_ack(self):
        writer = FakeWriter()
        clock = FakeClock()
        service = CommandService(writer=writer, key=KEY, session=10, now=clock)
        for i in range(1, 101):
            seq = service.send(Command(CommandId.PING))
            completed = CommandAck(
                MessageType.COMMAND,
                CommandId.PING,
                seq,
                AckStatus.COMPLETED,
            )
            service.on_ack(
                Frame(1, MessageType.COMMAND_RESULT, 0, 10, i, completed.to_payload())
            )
            self.assertTrue(service.pending.done)
        self.assertEqual(len(writer.writes), 100)

    def test_command_frame_payload_stays_under_limit(self):
        frame = pack_frame(
            MessageType.COMMAND,
            Command(CommandId.SET_TARGETS, 11, 12).to_payload(),
            session=1,
            seq=1,
            key=KEY,
        )
        self.assertLess(len(frame), 128)


if __name__ == "__main__":
    unittest.main()
