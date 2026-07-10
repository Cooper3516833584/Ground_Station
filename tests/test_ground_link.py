import time
import unittest

from ground_link import GroundStationLink
from models import (
    AckStatus,
    Command,
    CommandAck,
    CommandId,
    FCState,
    FLAG_UPLINK_WINDOW,
    MessageType,
    MissionState,
    MissionStatus,
)
from protocol import pack_frame


KEY = bytes.fromhex("00112233445566778899aabbccddeeff")


class FakeTransport:
    def __init__(self, **kwargs):
        self.on_bytes = kwargs["on_bytes"]
        self.on_connected = kwargs["on_connected"]
        self.on_disconnected = kwargs["on_disconnected"]
        self.writes = []
        self.connected = False

    def start(self):
        self.connected = True
        self.on_connected()

    def stop(self):
        self.connected = False

    def write(self, data):
        if not self.connected:
            raise RuntimeError("offline")
        self.writes.append(data)


def sample_state() -> FCState:
    return FCState(
        1000, -2000, 11.85, 3, True,
    )


class GroundLinkTests(unittest.TestCase):
    def setUp(self):
        self.links = []

    def tearDown(self):
        for link in self.links:
            link.close()

    def make_link(self, **callbacks):
        link = GroundStationLink(
            port="fake",
            key=KEY,
            uplink_delay_seconds=0.0,
            wake_repeats=2,
            wake_interval_seconds=0.0,
            wake_settle_seconds=0.0,
            command_burst_count=2,
            command_burst_interval_seconds=0.0,
            transport_factory=FakeTransport,
            **callbacks,
        )
        link.start()
        self.links.append(link)
        return link

    def test_receives_complete_state_and_mission_progress(self):
        states = []
        statuses = []
        link = self.make_link(
            on_fc_state=lambda value, session: states.append((value, session)),
            on_mission_status=lambda value, session: statuses.append((value, session)),
        )
        state_frame = pack_frame(
            MessageType.FC_STATE,
            sample_state().to_payload(),
            session=77,
            seq=1,
            key=KEY,
        )
        status = MissionStatus(
            MissionState.RUNNING, 2, 9, 45, 0, "heading to target 2"
        )
        status_frame = pack_frame(
            MessageType.MISSION_STATUS,
            status.to_payload(),
            session=77,
            seq=2,
            key=KEY,
        )
        link._transport.on_bytes(state_frame[:4])
        link._transport.on_bytes(state_frame[4:] + status_frame)
        self.assertEqual(states, [(sample_state(), 77)])
        self.assertEqual(statuses, [(status, 77)])

    def test_stop_can_be_sent_while_start_result_is_pending(self):
        link = self.make_link()
        start_seq = link.send_command(Command(CommandId.START_MISSION))
        received = CommandAck(
            MessageType.COMMAND,
            CommandId.START_MISSION,
            start_seq,
            AckStatus.RECEIVED,
        )
        link._transport.on_bytes(
            pack_frame(
                MessageType.COMMAND_ACK,
                received.to_payload(),
                session=77,
                seq=10,
                key=KEY,
            )
        )
        stop_seq = link.send_command(Command(CommandId.STOP_MISSION))
        self.assertNotEqual(start_seq, stop_seq)
        with self.assertRaises(RuntimeError):
            link.send_command(Command(CommandId.PING))

    def test_command_uses_wake_preamble_then_signed_burst(self):
        link = self.make_link()
        link._transport.on_bytes(
            pack_frame(
                MessageType.FC_STATE,
                sample_state().to_payload(),
                session=77,
                seq=1,
                key=KEY,
            )
        )
        link.send_command(Command(CommandId.PING))
        time.sleep(0.05)
        self.assertEqual(link._transport.writes, [])
        link._transport.on_bytes(
            pack_frame(
                MessageType.FC_STATE,
                sample_state().to_payload(),
                session=77,
                seq=2,
                key=KEY,
                flags=FLAG_UPLINK_WINDOW,
            )
        )
        deadline = time.monotonic() + 0.5
        while len(link._transport.writes) < 4 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(link._transport.writes[:2], [b"\x00", b"\x00"])
        command_frames = link._transport.writes[2:4]
        self.assertEqual(len(command_frames), 2)
        self.assertEqual(command_frames[0], command_frames[1])
        self.assertTrue(command_frames[0].startswith(b"\xA5\x5A"))


if __name__ == "__main__":
    unittest.main()
