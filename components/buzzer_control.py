from __future__ import annotations

import time
from types import ModuleType
from typing import Callable


DEFAULT_BUZZER_PIN = 27
DEFAULT_BUZZER_DURATION_SECONDS = 0.2


def _load_gpio() -> ModuleType:
    try:
        import RPi.GPIO as gpio
    except ImportError as exc:
        raise RuntimeError(
            "RPi.GPIO is required to control the ground-station buzzer"
        ) from exc
    return gpio


def trigger_buzzer(
    duration_seconds: float = DEFAULT_BUZZER_DURATION_SECONDS,
    *,
    pin: int = DEFAULT_BUZZER_PIN,
    gpio: ModuleType | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Sound the active-high buzzer once using Raspberry Pi BCM numbering."""
    if isinstance(duration_seconds, bool) or not isinstance(
        duration_seconds, (int, float)
    ):
        raise ValueError("buzzer duration_seconds must be a number")
    duration = float(duration_seconds)
    if duration <= 0:
        raise ValueError("buzzer duration_seconds must be greater than zero")
    if isinstance(pin, bool) or not isinstance(pin, int) or pin < 0:
        raise ValueError("buzzer pin must be a non-negative integer")

    gpio_driver = gpio if gpio is not None else _load_gpio()
    gpio_driver.setwarnings(False)
    gpio_driver.setmode(gpio_driver.BCM)
    gpio_driver.setup(pin, gpio_driver.OUT, initial=gpio_driver.LOW)
    try:
        gpio_driver.output(pin, gpio_driver.HIGH)
        sleep(duration)
    finally:
        gpio_driver.output(pin, gpio_driver.LOW)
        gpio_driver.cleanup(pin)
