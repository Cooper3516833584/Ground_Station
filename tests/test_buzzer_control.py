from __future__ import annotations

import unittest
from unittest.mock import Mock, call

from components.buzzer_control import build_ground_buzzer, trigger_buzzer


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

    def test_active_high_false_reverses_levels(self) -> None:
        gpio = FakeGPIO()
        sleep = Mock()

        trigger_buzzer(0.25, active_high=False, gpio=gpio, sleep=sleep)

        gpio.setup.assert_called_once_with(27, gpio.OUT, initial=gpio.HIGH)
        gpio.output.assert_has_calls([call(27, gpio.LOW), call(27, gpio.HIGH)])

    def test_custom_pin_is_used(self) -> None:
        gpio = FakeGPIO()
        sleep = Mock()

        trigger_buzzer(0.1, pin=17, gpio=gpio, sleep=sleep)

        gpio.setup.assert_called_once_with(17, gpio.OUT, initial=gpio.LOW)
        gpio.output.assert_has_calls([call(17, gpio.HIGH), call(17, gpio.LOW)])
        gpio.cleanup.assert_called_once_with(17)

    def test_custom_numbering_is_used(self) -> None:
        gpio = FakeGPIO()
        sleep = Mock()

        trigger_buzzer(0.1, numbering="BCM", gpio=gpio, sleep=sleep)

        gpio.setmode.assert_called_once_with(gpio.BCM)

    def test_disabled_buzzer_is_a_no_op_and_never_touches_gpio(self) -> None:
        gpio = FakeGPIO()
        sleep = Mock()

        trigger_buzzer(0.5, enabled=False, gpio=gpio, sleep=sleep)

        gpio.setwarnings.assert_not_called()
        gpio.setmode.assert_not_called()
        gpio.setup.assert_not_called()
        gpio.output.assert_not_called()
        gpio.cleanup.assert_not_called()
        sleep.assert_not_called()

    def test_disabled_buzzer_ignores_invalid_duration(self) -> None:
        gpio = FakeGPIO()
        # Even an invalid duration must not raise when the buzzer is disabled.
        trigger_buzzer(0, enabled=False, gpio=gpio, sleep=Mock())
        gpio.setup.assert_not_called()

    def test_build_ground_buzzer_uses_station_settings(self) -> None:
        class FakeBuzzerSettings:
            enabled = True
            pin = 21
            numbering = "BCM"
            active_high = False
            default_duration_seconds = 0.4

        class FakeStation:
            buzzer = FakeBuzzerSettings()

        callback = build_ground_buzzer(FakeStation())
        gpio = FakeGPIO()
        # The factory-built callback creates its own driver, so monkeypatch
        # the module loader to inject the fake.
        import components.buzzer_control as module

        original = module._load_gpio
        module._load_gpio = lambda: gpio
        try:
            callback(0.3)
        finally:
            module._load_gpio = original

        gpio.setup.assert_called_once_with(21, gpio.OUT, initial=gpio.HIGH)
        gpio.output.assert_has_calls([call(21, gpio.LOW), call(21, gpio.HIGH)])

    def test_build_ground_buzzer_disabled_is_no_op(self) -> None:
        class FakeBuzzerSettings:
            enabled = False
            pin = 21
            numbering = "BCM"
            active_high = True
            default_duration_seconds = 0.4

        class FakeStation:
            buzzer = FakeBuzzerSettings()

        callback = build_ground_buzzer(FakeStation())
        gpio = FakeGPIO()
        import components.buzzer_control as module

        original = module._load_gpio
        module._load_gpio = lambda: gpio
        try:
            callback(0.3)
        finally:
            module._load_gpio = original

        gpio.setup.assert_not_called()
        gpio.output.assert_not_called()

    def test_build_ground_buzzer_none_uses_station_default_duration(self) -> None:
        class FakeBuzzerSettings:
            enabled = True
            pin = 21
            numbering = "BCM"
            active_high = True
            default_duration_seconds = 0.4

        class FakeStation:
            buzzer = FakeBuzzerSettings()

        callback = build_ground_buzzer(FakeStation())
        gpio = FakeGPIO()
        slept = []

        def fake_sleep(duration):
            slept.append(duration)

        import components.buzzer_control as module

        original = module._load_gpio
        original_trigger = module.trigger_buzzer
        module._load_gpio = lambda: gpio

        def spy_trigger(duration, **kwargs):
            original_trigger(duration, gpio=gpio, sleep=fake_sleep, **kwargs)

        module.trigger_buzzer = spy_trigger
        try:
            callback(None)  # should use station default 0.4
            callback(0.3)  # explicit duration must win
        finally:
            module._load_gpio = original
            module.trigger_buzzer = original_trigger

        self.assertEqual(slept, [0.4, 0.3])


if __name__ == "__main__":
    unittest.main()
