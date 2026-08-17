"""Station configuration loader tests.

Fixture JSON files are written into the pre-existing ``.test_tmp/`` workspace
directory because the test environment forbids writes into directories that
the test process itself creates.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from unittest import mock

from components.station_config import (
    STATION_CONFIG_ENV,
    load_station_settings,
)

WORKSPACE = Path(__file__).resolve().parents[1]
SCRATCH = WORKSPACE / ".test_tmp"

VALID_CONFIG = {
    "hardware": {
        "led": {
            "enabled": True,
            "pin": 18,
            "count": 7,
            "frequency_hz": 800000,
            "dma": 10,
            "channel": 0,
            "invert": False,
            "strip_type": "WS2811_STRIP_GRB",
            "default_brightness": 3,
            "max_brightness": 20,
            "socket_path": "/run/ground-station-led.sock",
            "override_timeout_seconds": 30.0,
            "flow_interval_seconds": 0.16,
            "flow_color_step": 3,
        },
        "buzzer": {
            "enabled": True,
            "pin": 27,
            "numbering": "BCM",
            "active_high": True,
            "default_duration_seconds": 0.2,
        },
    },
    "serial": {
        "screen": {
            "port": "/dev/serial/by-id/test-screen",
            "baudrate": 9600,
            "read_timeout_seconds": 0.05,
        },
        "fleet_radio": {
            "port": "/dev/serial/by-id/test-hc14",
            "baudrate": 115200,
            "read_timeout_seconds": 0.1,
            "write_timeout_seconds": 0.5,
            "reconnect_seconds": 1.0,
        },
    },
}


def write_config(content: dict, name: str) -> Path:
    path = SCRATCH / name
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


class StationConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for leftover in SCRATCH.glob("*.json"):
            try:
                leftover.unlink()
            except OSError:
                pass

    def test_loads_complete_valid_config(self) -> None:
        path = write_config(VALID_CONFIG, "valid.json")
        station = load_station_settings(path)

        self.assertTrue(station.led.enabled)
        self.assertEqual(station.led.pin, 18)
        self.assertEqual(station.led.count, 7)
        self.assertEqual(station.led.frequency_hz, 800000)
        self.assertEqual(station.led.dma, 10)
        self.assertEqual(station.led.channel, 0)
        self.assertFalse(station.led.invert)
        self.assertEqual(station.led.strip_type, "WS2811_STRIP_GRB")
        self.assertEqual(station.led.default_brightness, 3)
        self.assertEqual(station.led.max_brightness, 20)
        self.assertEqual(station.led.socket_path, "/run/ground-station-led.sock")
        self.assertEqual(station.led.override_timeout_seconds, 30.0)
        self.assertEqual(station.led.flow_interval_seconds, 0.16)
        self.assertEqual(station.led.flow_color_step, 3)

        self.assertTrue(station.buzzer.enabled)
        self.assertEqual(station.buzzer.pin, 27)
        self.assertEqual(station.buzzer.numbering, "BCM")
        self.assertTrue(station.buzzer.active_high)
        self.assertEqual(station.buzzer.default_duration_seconds, 0.2)

        self.assertEqual(station.screen.port, "/dev/serial/by-id/test-screen")
        self.assertEqual(station.screen.baudrate, 9600)
        self.assertEqual(station.screen.read_timeout_seconds, 0.05)
        self.assertIsNone(station.screen.write_timeout_seconds)
        self.assertIsNone(station.screen.reconnect_seconds)

        self.assertEqual(station.fleet_radio.port, "/dev/serial/by-id/test-hc14")
        self.assertEqual(station.fleet_radio.baudrate, 115200)
        self.assertEqual(station.fleet_radio.read_timeout_seconds, 0.1)
        self.assertEqual(station.fleet_radio.write_timeout_seconds, 0.5)
        self.assertEqual(station.fleet_radio.reconnect_seconds, 1.0)

    def test_defaults_for_optional_fields(self) -> None:
        config = json.loads(json.dumps(VALID_CONFIG))
        config["hardware"]["led"]["enabled"] = False
        config["hardware"]["led"]["invert"] = True
        del config["hardware"]["buzzer"]["enabled"]
        del config["hardware"]["buzzer"]["active_high"]
        del config["serial"]["fleet_radio"]["write_timeout_seconds"]
        del config["serial"]["fleet_radio"]["reconnect_seconds"]
        path = write_config(config, "defaults.json")

        station = load_station_settings(path)
        self.assertFalse(station.led.enabled)
        self.assertTrue(station.led.invert)
        self.assertTrue(station.buzzer.enabled)
        self.assertTrue(station.buzzer.active_high)
        self.assertIsNone(station.fleet_radio.write_timeout_seconds)
        self.assertIsNone(station.fleet_radio.reconnect_seconds)

    def test_explicit_path_wins_over_environment(self) -> None:
        explicit = write_config(VALID_CONFIG, "explicit.json")
        env_path = write_config(VALID_CONFIG, "env.json")
        with mock.patch.dict(
            os.environ, {STATION_CONFIG_ENV: str(env_path)}, clear=False
        ):
            station = load_station_settings(explicit)
        self.assertEqual(station.screen.port, "/dev/serial/by-id/test-screen")

    def test_environment_variable_is_used_without_explicit_path(self) -> None:
        env_path = write_config(VALID_CONFIG, "env2.json")
        with mock.patch.dict(
            os.environ, {STATION_CONFIG_ENV: str(env_path)}, clear=False
        ):
            station = load_station_settings()
        self.assertEqual(station.fleet_radio.port, "/dev/serial/by-id/test-hc14")

    def test_default_local_path_is_tried_last(self) -> None:
        local = write_config(VALID_CONFIG, "station.local.json")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch(
                "components.station_config._default_station_path",
                return_value=local,
            ):
                station = load_station_settings()
        self.assertEqual(station.led.count, 7)

    def test_missing_config_raises_clear_error(self) -> None:
        missing = SCRATCH / "does-not-exist.json"
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "station configuration not found"):
                load_station_settings(missing)

    def test_missing_config_error_suggests_copy_command(self) -> None:
        missing = SCRATCH / "does-not-exist.json"
        with self.assertRaisesRegex(RuntimeError, "cp config/station.example.json"):
            load_station_settings(missing)

    def test_invalid_json_raises_value_error(self) -> None:
        path = SCRATCH / "broken.json"
        path.write_text("{ not json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            load_station_settings(path)

    def _assert_path_error(self, mutate, expected: str) -> None:
        config = json.loads(json.dumps(VALID_CONFIG))
        mutate(config)
        path = write_config(config, "invalid.json")
        with self.assertRaisesRegex(ValueError, expected):
            load_station_settings(path)

    def test_led_pin_below_zero_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["led"].__setitem__("pin", -1),
            "hardware.led.pin must be >= 0",
        )

    def test_led_count_zero_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["led"].__setitem__("count", 0),
            "hardware.led.count must be >= 1",
        )

    def test_brightness_above_255_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["led"].__setitem__("default_brightness", 256),
            "hardware.led.default_brightness must be <= 255",
        )

    def test_max_brightness_above_255_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["led"].__setitem__("max_brightness", 300),
            "hardware.led.max_brightness must be <= 255",
        )

    def test_led_frequency_non_positive_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["led"].__setitem__("frequency_hz", 0),
            "hardware.led.frequency_hz must be >= 1",
        )

    def test_led_dma_negative_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["led"].__setitem__("dma", -1),
            "hardware.led.dma must be >= 0",
        )

    def test_led_channel_negative_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["led"].__setitem__("channel", -1),
            "hardware.led.channel must be >= 0",
        )

    def test_led_socket_path_empty_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["led"].__setitem__("socket_path", ""),
            "hardware.led.socket_path must be a non-empty string",
        )

    def test_unknown_strip_type_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["led"].__setitem__("strip_type", "NOT_A_STRIP"),
            "hardware.led.strip_type must be one of",
        )

    def test_default_brightness_above_max_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["led"].__setitem__("default_brightness", 21),
            "must not exceed",
        )

    def test_baudrate_zero_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["serial"]["fleet_radio"].__setitem__("baudrate", 0),
            "serial.fleet_radio.baudrate must be >= 1",
        )

    def test_timeout_zero_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["serial"]["screen"].__setitem__("read_timeout_seconds", 0),
            "serial.screen.read_timeout_seconds must be > 0",
        )

    def test_empty_serial_port_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["serial"]["fleet_radio"].__setitem__("port", "  "),
            "serial.fleet_radio.port must be a non-empty string",
        )

    def test_buzzer_pin_below_zero_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["buzzer"].__setitem__("pin", -2),
            "hardware.buzzer.pin must be >= 0",
        )

    def test_unknown_numbering_is_rejected(self) -> None:
        self._assert_path_error(
            lambda c: c["hardware"]["buzzer"].__setitem__("numbering", "BOARD"),
            "hardware.buzzer.numbering must be one of",
        )


if __name__ == "__main__":
    unittest.main()
