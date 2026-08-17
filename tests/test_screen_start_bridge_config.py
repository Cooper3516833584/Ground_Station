"""Regression tests: ScreenStartBridge must use the station-configured LED client."""

import unittest
from unittest import mock

from components.led_control import GroundLedClient
from screen_start_bridge import ScreenStartBridge


class FakeLedSettings:
    socket_path = "/run/custom-led.sock"
    count = 7
    default_brightness = 3
    flow_interval_seconds = 0.16


def make_bridge(**overrides):
    kwargs = dict(
        transport=mock.Mock(),
        master=mock.Mock(),
        store=mock.Mock(),
        mission_config={},
        cooldown_seconds=0.5,
    )
    kwargs.update(overrides)
    return ScreenStartBridge(**kwargs)


class ScreenStartBridgeLedConfigTests(unittest.TestCase):
    def test_bridge_uses_injected_station_led_client_socket(self):
        led = GroundLedClient.from_settings(FakeLedSettings())
        bridge = make_bridge(led=led)
        self.assertIs(bridge._led, led)
        self.assertEqual(bridge._led._socket_path, "/run/custom-led.sock")

    def test_bridge_without_led_keeps_default_client(self):
        bridge = make_bridge()
        self.assertIsInstance(bridge._led, GroundLedClient)
        self.assertEqual(bridge._led._socket_path, "/run/ground-station-led.sock")

    def test_white_indicator_uses_bridge_led_client(self):
        # The LED indicator helpers must go through the injected client; with
        # the default (non-existent) socket they fail gracefully, proving the
        # call path uses the bridge client rather than any hardcoded socket.
        bridge = make_bridge(led=GroundLedClient.from_settings(FakeLedSettings()))
        with mock.patch.object(bridge._led, "blink") as blink:
            bridge._set_white_blink("test")
        blink.assert_called_once()
        self.assertEqual(blink.call_args.kwargs["brightness"], 20)


if __name__ == "__main__":
    unittest.main()
