import threading
import unittest

from components.competition_remote import CarCommandRemote, RemoteSettings, ScreenLineDecoder
from components.fleet_models import AckPayload, AckStatus, CommandId, Frame, MessageKind, NodeId
from components.fleet_protocol import decode_command, encode_ack, pack_frame, unpack_frame


class ScreenTests(unittest.TestCase):
    def test_split_messages_and_multiple_buttons(self):
        decoder = ScreenLineDecoder(RemoteSettings())
        self.assertEqual(decoder.feed(b"ru"), [])
        self.assertEqual(decoder.feed(b"n\nSTOP\r\nrun\n"), ["START", "STOP", "START"])

    def test_unknown_substrings_binary_and_oversize_do_not_start(self):
        decoder = ScreenLineDecoder(RemoteSettings(), max_line_bytes=8)
        self.assertEqual(decoder.feed(b"prerun\nSTOPPED\n\x65run\n"), [])
        self.assertEqual(decoder.feed(b"xxxxxxxxxrun\nrun\n"), ["START"])

    def test_reconnection_drops_partial_input(self):
        decoder = ScreenLineDecoder(RemoteSettings())
        decoder.feed(b"ru")
        decoder.reset()
        self.assertEqual(decoder.feed(b"n\n"), [])


class RemoteTests(unittest.TestCase):
    def make_remote(self, send, retries=1):
        class Transport:
            def write(self, frame):
                send(frame)
        events = []
        terminal = threading.Event()
        def emit(name, **data):
            events.append((name, data))
            if name in ("command_acknowledged", "command_timeout", "command_rejected"):
                terminal.set()
        remote = CarCommandRemote(Transport(), RemoteSettings(response_timeout_seconds=.03, command_retries=retries), emit, session=123)
        self.addCleanup(remote.close)
        return remote, events, terminal

    @staticmethod
    def ack(remote, raw, *, source=NodeId.CAR, session=None, status=AckStatus.COMPLETED):
        request = unpack_frame(raw)
        command = decode_command(request.payload)
        payload = encode_ack(AckPayload(request.session if session is None else session,
            request.seq, command.command_id, status))
        remote.feed_bytes(pack_frame(Frame(1, source, NodeId.GROUND, MessageKind.ACK, 0, 999, 1, payload)))

    def test_only_car_addressed_and_retries_preserve_exact_request(self):
        sent = []
        def send(raw):
            sent.append(raw)
            if len(sent) == 2:
                self.ack(remote, raw)
        remote, events, done = self.make_remote(send)
        remote.start()
        remote.submit("START")
        self.assertTrue(done.wait(1))
        self.assertEqual(sent[0], sent[1])
        frame = unpack_frame(sent[0])
        self.assertEqual((frame.src, frame.dst), (NodeId.GROUND, NodeId.CAR))
        self.assertEqual(decode_command(frame.payload).command_id, CommandId.CAR_START_MISSION)
        self.assertEqual(events[-1][0], "command_acknowledged")

    def test_wrong_source_or_session_ack_cannot_confirm(self):
        def send(raw):
            self.ack(remote, raw, source=NodeId.DRONE)
            self.ack(remote, raw, session=124)
        remote, events, done = self.make_remote(send, retries=0)
        remote.start()
        remote.submit("STOP")
        self.assertTrue(done.wait(1))
        self.assertEqual(events[-1][0], "command_timeout")

    def test_stop_interrupts_start_and_merges_repeated_button(self):
        sent = []
        started = threading.Event()
        def send(raw):
            command = decode_command(unpack_frame(raw).payload).command_id
            sent.append(command)
            if command == CommandId.CAR_START_MISSION:
                started.set()
            else:
                self.ack(remote, raw)
        remote, events, done = self.make_remote(send)
        remote.start()
        remote.submit("START")
        self.assertTrue(started.wait(1))
        self.assertFalse(remote.submit("START"))
        self.assertTrue(remote.submit("STOP"))
        self.assertFalse(remote.submit("STOP"))
        self.assertTrue(done.wait(1))
        self.assertEqual(sent, [CommandId.CAR_START_MISSION, CommandId.TARGETED_STOP])
        self.assertTrue(any(name == "command_cancelled" for name, _ in events))

    def test_ack_accepted_is_delivery_not_mission_completion(self):
        def send(raw):
            self.ack(remote, raw, status=AckStatus.ACCEPTED)
        remote, events, done = self.make_remote(send)
        remote.start()
        remote.submit("START")
        self.assertTrue(done.wait(1))
        self.assertEqual(events[-1][1]["ack_status"], AckStatus.ACCEPTED)

    def test_repeated_stop_keeps_stop_and_removes_queued_start(self):
        sent = []
        def send(raw):
            sent.append(decode_command(unpack_frame(raw).payload).command_id)
            self.ack(remote, raw)
        remote, events, done = self.make_remote(send)
        remote.submit("STOP")
        remote.submit("START")
        self.assertFalse(remote.submit("STOP"))
        remote.start()
        self.assertTrue(done.wait(1))
        remote.close()
        self.assertEqual(sent, [CommandId.TARGETED_STOP])

    def test_failed_stop_cancels_start_waiting_behind_it(self):
        sent = []
        def send(raw):
            sent.append(decode_command(unpack_frame(raw).payload).command_id)
        remote, events, done = self.make_remote(send, retries=0)
        remote.submit("STOP")
        remote.submit("START")
        remote.start()
        self.assertTrue(done.wait(1))
        remote.close()
        self.assertEqual(sent, [CommandId.TARGETED_STOP])

    def test_delayed_received_cannot_replace_completion(self):
        def send(raw):
            self.ack(remote, raw, status=AckStatus.COMPLETED)
            self.ack(remote, raw, status=AckStatus.RECEIVED)
        remote, events, done = self.make_remote(send, retries=0)
        remote.start()
        remote.submit("STOP")
        self.assertTrue(done.wait(1))
        self.assertEqual(events[-1][0], "command_acknowledged")
        self.assertEqual(events[-1][1]["ack_status"], AckStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
