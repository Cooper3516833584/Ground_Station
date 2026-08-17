from __future__ import annotations

from dataclasses import dataclass
import time
from types import ModuleType
from typing import Callable

# Fallback defaults matching the original station hardware.  Entry programs
# build the buzzer callback from the station configuration instead.
DEFAULT_BUZZER_PIN = 27
DEFAULT_BUZZER_DURATION_SECONDS = 0.2


@dataclass(frozen=True)
class BuzzerSettings:
    """Hardware settings for the ground-station buzzer.

    ``enabled=False`` turns the buzzer into a safe no-op: ``RPi.GPIO`` is
    never imported and no GPIO call is made, so a machine without a buzzer
    (or without RPi.GPIO installed) can still run the whole ground station.
    """

    enabled: bool = True
    pin: int = DEFAULT_BUZZER_PIN
    numbering: str = "BCM"
    active_high: bool = True
    default_duration_seconds: float = DEFAULT_BUZZER_DURATION_SECONDS


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
    numbering: str = "BCM",
    active_high: bool = True,
    enabled: bool = True,
    gpio: ModuleType | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Sound the buzzer once using Raspberry Pi BCM numbering.

    With ``active_high=True`` the buzzer is driven HIGH while sounding and
    LOW otherwise; with ``active_high=False`` the levels are reversed.
    With ``enabled=False`` this is a safe no-op and GPIO is never touched.
    """
    if not enabled:
        return
    if isinstance(duration_seconds, bool) or not isinstance(
        duration_seconds, (int, float)
    ):
        raise ValueError("buzzer duration_seconds must be a number")
    duration = float(duration_seconds)
    if duration <= 0:
        raise ValueError("buzzer duration_seconds must be greater than zero")
    if isinstance(pin, bool) or not isinstance(pin, int) or pin < 0:
        raise ValueError("buzzer pin must be a non-negative integer")
    if not isinstance(numbering, str) or not numbering:
        raise ValueError("buzzer numbering must be a non-empty string")

    gpio_driver = gpio if gpio is not None else _load_gpio()
    on_level = gpio_driver.HIGH if active_high else gpio_driver.LOW
    off_level = gpio_driver.LOW if active_high else gpio_driver.HIGH
    gpio_driver.setwarnings(False)
    gpio_driver.setmode(getattr(gpio_driver, numbering))
    gpio_driver.setup(pin, gpio_driver.OUT, initial=off_level)
    try:
        gpio_driver.output(pin, on_level)
        sleep(duration)
    finally:
        gpio_driver.output(pin, off_level)
        gpio_driver.cleanup(pin)


def build_ground_buzzer(station) -> Callable[[float | None], None]:
    """Build a buzzer callback from a ``StationSettings``.

    The returned callback respects ``hardware.buzzer.enabled`` (a disabled
    buzzer becomes a safe no-op).  Calling it with ``None`` uses
    ``hardware.buzzer.default_duration_seconds``; callers that pass an
    explicit duration keep their value unchanged.
    """
    buzzer = station.buzzer

    def _callback(duration_seconds: float | None = None) -> None:
        if duration_seconds is None:
            duration_seconds = buzzer.default_duration_seconds
        trigger_buzzer(
            duration_seconds,
            pin=buzzer.pin,
            numbering=buzzer.numbering,
            active_high=buzzer.active_high,
            enabled=buzzer.enabled,
        )

    return _callback
