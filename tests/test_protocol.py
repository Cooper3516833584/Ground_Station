import random
import unittest

from models import (
    FC_STATE_STRUCT,
    FCState,
    LEDControl,
    LEDMode,
    MessageType,
    TelemetryExtension,
)
from protocol import (
    FAST_TELEMETRY_LEN,
    FastTelemetryParser,
    FrameParser,
    crc16_ccitt,
    pack_fast_telemetry,
    pack_frame,
    unpack_frame,
)


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

    def test_fast_telemetry_fragment_parser_and_crc(self):
        payload = FCState(123, -456, 12.34, 3, True).to_payload()
        frame = pack_fast_telemetry(payload=payload, session=77, seq=8)
        self.assertEqual(len(frame), FAST_TELEMETRY_LEN)
        parser = FastTelemetryParser()
        parsed = []
        for chunk in (frame[:1], frame[1:7], frame[7:19], frame[19:]):
            parsed.extend(parser.feed(chunk))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].session, 77)
        self.assertEqual(parsed[0].seq, 8)
        self.assertEqual(FCState.from_payload(parsed[0].payload).pos_y_cm, -456)

        corrupted = bytearray(frame)
        corrupted[-1] ^= 0x01
        self.assertEqual(FastTelemetryParser().feed(corrupted), [])

    def test_led_control_round_trip(self):
        control = LEDControl(
            LEDMode.PIXELS,
            brightness=5,
            pixels=((255, 0, 0),) * 7,
        )
        self.assertEqual(LEDControl.from_payload(control.to_payload()), control)
        self.assertEqual(
            LEDControl.from_payload(LEDControl(LEDMode.FLOW).to_payload()).mode,
            LEDMode.FLOW,
        )


if __name__ == "__main__":
    unittest.main()
