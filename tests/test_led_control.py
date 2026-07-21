import json
import unittest
from unittest import mock

from components.led_control import GroundLedClient, LED_CONTROL_PREFIX
from led_daemon import color_wheel, flow_pixels, parse_control


class FakeSocket:
    def __init__(self):
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def sendto(self, payload, path):
        self.sent.append((payload, path))


class LedControlTests(unittest.TestCase):
    def test_flow_has_one_moving_pixel_with_gradual_color_change(self):
        first = flow_pixels(0)
        second = flow_pixels(1)
        self.assertEqual(sum(pixel != (0, 0, 0) for pixel in first), 1)
        self.assertEqual(sum(pixel != (0, 0, 0) for pixel in second), 1)
        self.assertNotEqual(first, second)
        self.assertEqual(first[0], color_wheel(0))
        self.assertEqual(second[1], color_wheel(3))
        color_delta = sum(abs(a - b) for a, b in zip(first[0], second[1]))
        self.assertLessEqual(color_delta, 18)

    def test_single_call_encodes_blink_mode(self):
        fake = FakeSocket()
        with mock.patch("components.led_control.socket.socket", return_value=fake):
            GroundLedClient("/tmp/test-led.sock").blink(
                (10, 20, 30), brightness=4, interval_seconds=0.25
            )
        payload, path = fake.sent[0]
        self.assertEqual(path, "/tmp/test-led.sock")
        self.assertTrue(payload.startswith(LED_CONTROL_PREFIX))
        data = json.loads(payload[len(LED_CONTROL_PREFIX) :])
        self.assertEqual(data["mode"], "blink")
        self.assertEqual(data["color"], [10, 20, 30])
        self.assertEqual(data["brightness"], 4)
        daemon_control = parse_control(payload)
        self.assertEqual(daemon_control["mode"], "blink")
        self.assertEqual(daemon_control["color"], (10, 20, 30))
        self.assertEqual(daemon_control["interval_seconds"], 0.25)

    def test_rejects_bad_brightness_and_pixel_count(self):
        client = GroundLedClient("/tmp/test-led.sock")
        with self.assertRaises(ValueError):
            client.solid((255, 0, 0), brightness=21)
        with self.assertRaisesRegex(ValueError, "exactly 7"):
            client.pixels(((0, 0, 0),) * 6)


if __name__ == "__main__":
    unittest.main()
