"""Regression tests: GroundStationController honours the station configuration.

``app.py`` imports PyQt5 at module level.  PyQt5 is part of requirements.txt,
so this module uses the real PyQt5 and only skips when it is not installed
(same pattern as test_map_widget.py).  It never patches ``sys.modules``, so it
cannot affect the collection or execution of other tests.
"""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtWidgets import QApplication

    import app
except ImportError:
    app = None


def _settings():
    return SimpleNamespace(
        serial_port="OLD_PORT",
        baudrate=9600,
        hmac_key=b"deadbeef",
        telemetry_stale_seconds=1.5,
        command_timeout_seconds=0.8,
        command_retries=3,
    )


def _station(write_timeout=0.6, reconnect=1.5):
    return SimpleNamespace(
        led=SimpleNamespace(
            socket_path="/run/custom-led.sock",
            count=8,
            default_brightness=5,
            flow_interval_seconds=0.3,
        ),
        fleet_radio=SimpleNamespace(
            port="/dev/ttyCUSTOM",
            baudrate=115200,
            read_timeout_seconds=0.2,
            write_timeout_seconds=write_timeout,
            reconnect_seconds=reconnect,
        ),
    )


@unittest.skipIf(app is None, "PyQt5 is not installed in this environment")
class GroundStationControllerConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _controller(self, store, station=None):
        with mock.patch.object(app, "GroundStationLink") as link_cls, mock.patch.object(
            app, "GroundLedClient"
        ) as led_cls, mock.patch.object(app, "load_settings", return_value=_settings()):
            controller = app.GroundStationController(store, station=station)
        return controller, link_cls, led_cls

    def test_with_station_uses_station_radio_and_led_socket(self):
        store = SimpleNamespace(stale_after_seconds=None, link=SimpleNamespace())
        _controller, link_cls, led_cls = self._controller(store, station=_station())

        kwargs = link_cls.call_args.kwargs
        self.assertEqual(kwargs["port"], "/dev/ttyCUSTOM")
        self.assertEqual(kwargs["baudrate"], 115200)
        self.assertEqual(kwargs["read_timeout_seconds"], 0.2)
        self.assertEqual(kwargs["write_timeout_seconds"], 0.6)
        self.assertEqual(kwargs["reconnect_seconds"], 1.5)
        self.assertEqual(kwargs["key"], b"deadbeef")
        led_cls.from_settings.assert_called_once_with(_station().led)

    def test_with_station_none_timeouts_fall_back_to_defaults(self):
        store = SimpleNamespace(stale_after_seconds=None, link=SimpleNamespace())
        station = _station(write_timeout=None, reconnect=None)
        _controller, link_cls, _led_cls = self._controller(store, station=station)

        kwargs = link_cls.call_args.kwargs
        self.assertEqual(kwargs["write_timeout_seconds"], 0.5)
        self.assertEqual(kwargs["reconnect_seconds"], 1.0)

    def test_without_station_keeps_legacy_behavior(self):
        store = SimpleNamespace(stale_after_seconds=None, link=SimpleNamespace())
        _controller, link_cls, led_cls = self._controller(store, station=None)

        kwargs = link_cls.call_args.kwargs
        self.assertEqual(kwargs["port"], "OLD_PORT")
        self.assertEqual(kwargs["baudrate"], 9600)
        self.assertNotIn("read_timeout_seconds", kwargs)
        led_cls.assert_called_once_with()
        led_cls.from_settings.assert_not_called()

    def test_pyqt5_modules_are_not_replaced_by_fakes(self):
        # Regression guard: this test module must never leave a homemade fake
        # module in sys.modules (the real PyQt5 must stay importable).
        self.assertEqual(sys.modules["PyQt5"].__name__, "PyQt5")
        self.assertTrue(hasattr(sys.modules["PyQt5"], "QtCore"))
        self.assertTrue(hasattr(sys.modules["PyQt5"], "QtWidgets"))


if __name__ == "__main__":
    unittest.main()
