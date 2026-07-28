from __future__ import annotations

import unittest
from unittest.mock import Mock, call

from components.buzzer_control import trigger_buzzer


class FakeGPIO:
    BCM = 11
    OUT = 1
    LOW = 0
    HIGH = 1

    def __init__(self) -> None:
        self.setwarnings = Mock()
        self.setmode = Mock()
        self.setup = Mock()
        self.output = Mock()
        self.cleanup = Mock()


class BuzzerControlTests(unittest.TestCase):
    def test_trigger_buzzer_pulses_gpio27_high(self) -> None:
        gpio = FakeGPIO()
        sleep = Mock()

        trigger_buzzer(0.35, gpio=gpio, sleep=sleep)

        gpio.setwarnings.assert_called_once_with(False)
        gpio.setmode.assert_called_once_with(gpio.BCM)
        gpio.setup.assert_called_once_with(27, gpio.OUT, initial=gpio.LOW)
        gpio.output.assert_has_calls([call(27, gpio.HIGH), call(27, gpio.LOW)])
        sleep.assert_called_once_with(0.35)
        gpio.cleanup.assert_called_once_with(27)

    def test_trigger_buzzer_turns_off_after_sleep_error(self) -> None:
        gpio = FakeGPIO()
        sleep = Mock(side_effect=RuntimeError("interrupted"))

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            trigger_buzzer(gpio=gpio, sleep=sleep)

        gpio.output.assert_has_calls([call(27, gpio.HIGH), call(27, gpio.LOW)])
        gpio.cleanup.assert_called_once_with(27)

    def test_trigger_buzzer_rejects_invalid_duration(self) -> None:
        gpio = FakeGPIO()

        for value in (0, -1, True, "0.2"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    trigger_buzzer(value, gpio=gpio)

        gpio.setup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
