import json
import unittest
from unittest import mock

from components.led_control import (
    DEFAULT_LED_COUNT,
    GroundLedClient,
    LED_CONTROL_PREFIX,
)
from led_daemon import (
    color_wheel,
    configure_max_brightness,
    flow_pixels,
    max_brightness as daemon_max_brightness,
    parse_control,
    render_pattern,
)


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
                (10, 20, 30), brightness=255, interval_seconds=0.25
            )
        payload, path = fake.sent[0]
        self.assertEqual(path, "/tmp/test-led.sock")
        self.assertTrue(payload.startswith(LED_CONTROL_PREFIX))
        data = json.loads(payload[len(LED_CONTROL_PREFIX) :])
        self.assertEqual(data["mode"], "blink")
        self.assertEqual(data["color"], [10, 20, 30])
        self.assertEqual(data["brightness"], 255)
        daemon_control = parse_control(payload)
        self.assertEqual(daemon_control["mode"], "blink")
        self.assertEqual(daemon_control["color"], (10, 20, 30))
        self.assertEqual(daemon_control["brightness"], 255)
        self.assertEqual(daemon_control["interval_seconds"], 0.25)

    def test_rejects_bad_brightness_and_pixel_count(self):
        client = GroundLedClient("/tmp/test-led.sock")
        with self.assertRaises(ValueError):
            client.solid((255, 0, 0), brightness=256)
        with self.assertRaisesRegex(ValueError, "exactly 7"):
            client.pixels(((0, 0, 0),) * 6)

    def test_seven_led_configuration_still_works(self):
        client = GroundLedClient("/tmp/test-led.sock", pixel_count=7)
        self.assertEqual(client.pixel_count, 7)
        fake = FakeSocket()
        with mock.patch("components.led_control.socket.socket", return_value=fake):
            client.pixels(((255, 255, 255),) * 7, brightness=4)
        payload, _path = fake.sent[0]
        data = json.loads(payload[len(LED_CONTROL_PREFIX) :])
        self.assertEqual(len(data["pixels"]), 7)

    def test_eight_led_configuration_sends_eight_pixels(self):
        client = GroundLedClient("/tmp/test-led.sock", pixel_count=8)
        fake = FakeSocket()
        with mock.patch("components.led_control.socket.socket", return_value=fake):
            client.pixels(((255, 255, 255),) * 8, brightness=4)
        payload, _path = fake.sent[0]
        data = json.loads(payload[len(LED_CONTROL_PREFIX) :])
        self.assertEqual(len(data["pixels"]), 8)
        # The daemon must accept the 8-pixel payload for the same count.
        self.assertIsNot(parse_control(payload, count=8), False)

    def test_pixel_count_mismatch_is_rejected(self):
        client = GroundLedClient("/tmp/test-led.sock", pixel_count=8)
        with self.assertRaisesRegex(ValueError, "exactly 8"):
            client.pixels(((0, 0, 0),) * 7)

    def test_socket_path_can_be_customized(self):
        client = GroundLedClient("/custom/led.sock", pixel_count=7)
        fake = FakeSocket()
        with mock.patch("components.led_control.socket.socket", return_value=fake):
            client.off()
        self.assertEqual(fake.sent[0][1], "/custom/led.sock")

    def test_from_settings_uses_configured_socket_and_count(self):
        class FakeLedSettings:
            socket_path = "/run/station-led.sock"
            count = 12
            default_brightness = 3
            flow_interval_seconds = 0.16

        client = GroundLedClient.from_settings(FakeLedSettings())
        self.assertEqual(client.pixel_count, 12)
        fake = FakeSocket()
        with mock.patch("components.led_control.socket.socket", return_value=fake):
            client.pixels(((1, 2, 3),) * 12)
        self.assertEqual(fake.sent[0][1], "/run/station-led.sock")

    def test_default_led_count_fallback_is_seven(self):
        self.assertEqual(DEFAULT_LED_COUNT, 7)
        self.assertEqual(GroundLedClient("/tmp/x.sock").pixel_count, 7)

    def test_daemon_flow_pixels_respects_configured_count(self):
        eight = flow_pixels(0, count=8)
        self.assertEqual(len(eight), 8)
        self.assertEqual(sum(pixel != (0, 0, 0) for pixel in eight), 1)
        self.assertEqual(eight[0], color_wheel(0))

    def test_from_settings_flow_uses_station_defaults(self):
        class FakeLedSettings:
            socket_path = "/run/custom-led.sock"
            count = 7
            default_brightness = 5
            flow_interval_seconds = 0.33

        client = GroundLedClient.from_settings(FakeLedSettings())
        fake = FakeSocket()
        with mock.patch("components.led_control.socket.socket", return_value=fake):
            client.flow()
        payload, _path = fake.sent[0]
        data = json.loads(payload[len(LED_CONTROL_PREFIX) :])
        self.assertEqual(data["mode"], "flow")
        self.assertEqual(data["brightness"], 5)
        self.assertEqual(data["interval_seconds"], 0.33)

    def test_plain_client_flow_keeps_historical_defaults(self):
        client = GroundLedClient("/tmp/test-led.sock")
        fake = FakeSocket()
        with mock.patch("components.led_control.socket.socket", return_value=fake):
            client.flow()
        payload, _path = fake.sent[0]
        data = json.loads(payload[len(LED_CONTROL_PREFIX) :])
        self.assertEqual(data["brightness"], 3)
        self.assertEqual(data["interval_seconds"], 0.16)

    def test_flow_explicit_arguments_still_win(self):
        client = GroundLedClient.from_settings(
            type("Led", (), {"socket_path": "/x", "count": 7, "default_brightness": 5, "flow_interval_seconds": 0.33})()
        )
        fake = FakeSocket()
        with mock.patch("components.led_control.socket.socket", return_value=fake):
            client.flow(brightness=9, interval_seconds=0.7)
        payload, _path = fake.sent[0]
        data = json.loads(payload[len(LED_CONTROL_PREFIX) :])
        self.assertEqual(data["brightness"], 9)
        self.assertEqual(data["interval_seconds"], 0.7)


class LedDaemonBrightnessCapTests(unittest.TestCase):
    def setUp(self):
        self._original = daemon_max_brightness

    def tearDown(self):
        configure_max_brightness(self._original)

    def _render(self, brightness):
        class FakeStrip:
            def __init__(self):
                self.brightness = None
                self.pixels = []

            def setBrightness(self, value):
                self.brightness = value

            def setPixelColor(self, index, color):
                self.pixels.append((index, color))

            def show(self):
                pass

        strip = FakeStrip()
        pattern = {
            "mode": "solid",
            "brightness": brightness,
            "interval_seconds": 0.5,
            "color": (255, 0, 0),
            "pixels": None,
            "expires_at": None,
        }
        render_pattern(strip, pattern, 0, lambda r, g, b: (r, g, b))
        return strip

    def test_max_brightness_clamps_requested_brightness(self):
        configure_max_brightness(20)
        strip = self._render(255)
        self.assertEqual(strip.brightness, 20)

    def test_max_brightness_keeps_low_request(self):
        configure_max_brightness(20)
        strip = self._render(4)
        self.assertEqual(strip.brightness, 4)

    def test_default_cap_does_not_limit(self):
        strip = self._render(255)
        self.assertEqual(strip.brightness, 255)


if __name__ == "__main__":
    unittest.main()
