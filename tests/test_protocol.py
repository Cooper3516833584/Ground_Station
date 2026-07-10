import random
import unittest

from models import FC_STATE_STRUCT, FCState, MessageType, TelemetryExtension
from protocol import FrameParser, crc16_ccitt, pack_frame, unpack_frame


KEY = bytes.fromhex("00112233445566778899aabbccddeeff")


class ProtocolTests(unittest.TestCase):
    def test_crc16_ccitt_known_vector(self):
        self.assertEqual(crc16_ccitt(b"123456789"), 0x29B1)

    def test_compact_fc_state_and_extension_round_trip(self):
        state = FCState(
            pos_x_cm=1000,
            pos_y_cm=-2000,
            battery_v=11.1,
            mode=3,
            unlock=True,
            extensions=(TelemetryExtension(32, b"abc"),),
        )
        payload = state.to_payload()
        self.assertEqual(len(payload), 18)
        self.assertEqual(FC_STATE_STRUCT.size, 13)
        decoded = FCState.from_payload(payload)
        self.assertEqual(decoded.pos_x_m, 10.0)
        self.assertAlmostEqual(decoded.battery_v, 11.1)
        self.assertEqual(decoded.extension(32), b"abc")

    def test_random_fragment_parser(self):
        frames = [
            pack_frame(MessageType.HEARTBEAT, bytes([i]), session=0xABCDEF01, seq=i, key=KEY)
            for i in range(1, 25)
        ]
        stream = b"noise" + b"".join(frames)
        rng = random.Random(20260710)
        sizes = [rng.randint(1, 9) for _ in range(len(stream))]
        parser = FrameParser(key=KEY)
        parsed = []
        pos = 0
        for size in sizes:
            if pos >= len(stream):
                break
            parsed.extend(parser.feed(stream[pos : pos + size]))
            pos += size
        parsed.extend(parser.feed(stream[pos:]))
        self.assertEqual([frame.seq for frame in parsed], list(range(1, 25)))
        self.assertEqual(parser.stats.discarded_bytes, 5)

    def test_corrupted_frame_is_rejected(self):
        frame = bytearray(
            pack_frame(MessageType.HEARTBEAT, b"ok", session=1, seq=2, key=KEY)
        )
        frame[15] ^= 0x55
        parser = FrameParser(key=KEY)
        self.assertEqual(parser.feed(bytes(frame)), [])
        self.assertEqual(parser.stats.crc_failures, 1)

    def test_hmac_rejects_wrong_key(self):
        frame = pack_frame(MessageType.HEARTBEAT, b"ok", session=1, seq=2, key=KEY)
        with self.assertRaises(ValueError):
            unpack_frame(frame, key=b"wrong-key")


if __name__ == "__main__":
    unittest.main()
