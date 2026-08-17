"""Regression tests: QR task HC-14 CLI overrides must reach the controller.

``qr_number_display_task.py`` imports PyQt5 at module level, so these tests
only run where PyQt5 is installed (mirroring the test_map_widget pattern).
"""

from __future__ import annotations

import unittest

from components.station_config import (
    BuzzerHardwareSettings,
    LedHardwareSettings,
    SerialDeviceSettings,
    StationSettings,
)

try:
    from qr_number_display_task import apply_hc14_cli_overrides
except ImportError:
    apply_hc14_cli_overrides = None


def make_station():
    led = LedHardwareSettings(
        enabled=True,
        pin=18,
        count=7,
        frequency_hz=800000,
        dma=10,
        channel=0,
        invert=False,
        strip_type="WS2811_STRIP_GRB",
        default_brightness=3,
        max_brightness=20,
        socket_path="/run/ground-station-led.sock",
        override_timeout_seconds=30.0,
        flow_interval_seconds=0.16,
        flow_color_step=3,
    )
    buzzer = BuzzerHardwareSettings(
        enabled=True,
        pin=27,
        numbering="BCM",
        active_high=True,
        default_duration_seconds=0.2,
    )
    screen = SerialDeviceSettings(
        port="/dev/ttySCREEN", baudrate=9600, read_timeout_seconds=0.05
    )
    fleet_radio = SerialDeviceSettings(
        port="/dev/ttySTATION",
        baudrate=115200,
        read_timeout_seconds=0.1,
        write_timeout_seconds=0.5,
        reconnect_seconds=1.0,
    )
    return StationSettings(led=led, buzzer=buzzer, screen=screen, fleet_radio=fleet_radio)


@unittest.skipIf(
    apply_hc14_cli_overrides is None, "PyQt5 is not installed in this environment"
)
class QrHc14CliOverrideTests(unittest.TestCase):
    def test_cli_port_and_baud_override_station(self):
        station = make_station()
        effective = apply_hc14_cli_overrides(station, "/dev/ttyCLI", 57600)
        self.assertEqual(effective.fleet_radio.port, "/dev/ttyCLI")
        self.assertEqual(effective.fleet_radio.baudrate, 57600)

    def test_no_cli_override_keeps_station_values(self):
        station = make_station()
        effective = apply_hc14_cli_overrides(station, None, None)
        self.assertEqual(effective.fleet_radio.port, "/dev/ttySTATION")
        self.assertEqual(effective.fleet_radio.baudrate, 115200)

    def test_port_only_override(self):
        station = make_station()
        effective = apply_hc14_cli_overrides(station, "/dev/ttyCLI", None)
        self.assertEqual(effective.fleet_radio.port, "/dev/ttyCLI")
        self.assertEqual(effective.fleet_radio.baudrate, 115200)

    def test_baud_only_override(self):
        station = make_station()
        effective = apply_hc14_cli_overrides(station, None, 57600)
        self.assertEqual(effective.fleet_radio.port, "/dev/ttySTATION")
        self.assertEqual(effective.fleet_radio.baudrate, 57600)

    def test_original_station_is_not_mutated(self):
        station = make_station()
        apply_hc14_cli_overrides(station, "/dev/ttyCLI", 57600)
        self.assertEqual(station.fleet_radio.port, "/dev/ttySTATION")
        self.assertEqual(station.fleet_radio.baudrate, 115200)

    def test_screen_and_led_settings_are_untouched(self):
        station = make_station()
        effective = apply_hc14_cli_overrides(station, "/dev/ttyCLI", 57600)
        self.assertEqual(effective.screen.port, "/dev/ttySCREEN")
        self.assertEqual(effective.led.count, 7)
        self.assertEqual(effective.led.socket_path, "/run/ground-station-led.sock")


if __name__ == "__main__":
    unittest.main()
