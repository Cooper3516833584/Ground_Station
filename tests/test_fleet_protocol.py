import json
from pathlib import Path
import unittest

from components.fleet_models import *
from components.fleet_protocol import *


DATA = json.loads((Path(__file__).parent / "data" / "fleetbus_v1_golden.json").read_text())


class FleetProtocolTests(unittest.TestCase):
    def test_crc_standard_vector(self):
        self.assertEqual(crc16_ccitt_false(b"123456789"), 0x29B1)

    def test_all_golden_frames_round_trip_exactly(self):
        for item in DATA["valid_frames"]:
            raw = bytes.fromhex(item["frame_hex"])
            frame = unpack_frame(raw)
            self.assertEqual(frame.payload.hex(), item["payload_hex"], item["name"])
            self.assertEqual(pack_frame(frame), raw, item["name"])

    def test_single_byte_fragmentation_and_embedded_delimiters(self):
        raw = bytes.fromhex(DATA["scenarios"]["fragmentation_hex"])
        parser = FrameParser()
        frames = []
        for byte in raw:
            frames.extend(parser.feed(bytes((byte,))))
        self.assertEqual(len(frames), 1)
        self.assertIn(MAGIC + TAIL, frames[0].payload)

    def test_sticky_frames(self):
        parser = FrameParser()
        frames = parser.feed(bytes.fromhex(DATA["scenarios"]["sticky_hex"]))
        self.assertEqual(len(frames), 2)

    def test_bad_frame_recovers_without_losing_following_frame(self):
        bad = bytes.fromhex(DATA["scenarios"]["bad_crc_hex"])
        good = bytes.fromhex(DATA["valid_frames"][0]["frame_hex"])
        parser = FrameParser()
        self.assertEqual(parser.feed(bad + good), [unpack_frame(good)])
        self.assertEqual(parser.stats.crc_failures, 1)

    def test_truncated_frame_waits(self):
        parser = FrameParser()
        self.assertEqual(parser.feed(bytes.fromhex(DATA["scenarios"]["truncated_hex"])), [])

    def test_address_filter(self):
        raw = bytes.fromhex(DATA["valid_frames"][0]["frame_hex"])
        parser = FrameParser(local_node=NodeId.CAR)
        self.assertEqual(parser.feed(raw), [])
        self.assertEqual(parser.stats.address_drops, 1)

    def test_payload_codecs(self):
        report = ReportPayload(1, 2, 3, 4, -5, 6, 7, 800, 9, -10, 11, 1200, 4, 3, 12, 2, 0)
        self.assertEqual(decode_report(encode_report(report)), report)
        ack = AckPayload(1, 2, CommandId.PING, AckStatus.COMPLETED, AckReason.NONE, "ok")
        self.assertEqual(decode_ack(encode_ack(ack)), ack)
        coordinate = CoordinateFrameCommand(10, -20, 35999)
        self.assertEqual(decode_coordinate_frame(encode_coordinate_frame(coordinate)), coordinate)
        car = CarNavigateCommand(100, -50, 9000)
        self.assertEqual(decode_car_navigate(encode_car_navigate(car)), car)
        drone = DroneGotoCommand(100, -50, 120, None)
        self.assertEqual(decode_drone_goto(encode_drone_goto(drone)), drone)
        map_value = MapReportPayload(1, 2, 3, ((0, 0), (1, 0), (1, 1), (0, 1)))
        self.assertEqual(decode_map_report(encode_map_report(map_value)), map_value)
        path = PathReportPayload(1, 2, 3, ((0, 0), (5, 6)))
        self.assertEqual(decode_path_report(encode_path_report(path)), path)

    def test_sequence_and_session_cache(self):
        counter = SequenceCounter(0xFFFE)
        self.assertEqual((counter.next(), counter.next(), counter.next()), (0xFFFF, 1, 2))
        cache = RecentResponseCache(2)
        cache.put(10, 1, b"a")
        self.assertEqual(cache.get(10, 1), b"a")
        cache.begin_ground_session(11)
        self.assertIsNone(cache.get(10, 1))


if __name__ == "__main__":
    unittest.main()
