"""Ground-station machine configuration.

This module is the single public entry point for reading the *machine*
configuration of a ground station: GPIO pins, LED count, serial device paths,
baudrates, and hardware enable/disable flags.

It intentionally knows nothing about the competition task rules, the FleetBus
protocol, or any algorithm.  Task-level parameters live in the task/fleet JSON
configs; protocol constants stay in Python (see docs/architecture.md and
docs/configuration.md for the split).

Configuration lookup order for ``load_station_settings``:

1. explicit ``path`` argument (CLI ``--station-config``);
2. ``GROUND_STATION_CONFIG`` environment variable;
3. ``config/station.local.json`` (relative to the working directory, then
   relative to the project root);
4. otherwise a clear error telling the user to copy the example first.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping

STATION_CONFIG_ENV = "GROUND_STATION_CONFIG"
LOCAL_CONFIG_RELPATH = "config/station.local.json"
EXAMPLE_CONFIG_RELPATH = "config/station.example.json"

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The only numbering schemes the station config currently accepts.
SUPPORTED_NUMBERING = frozenset({"BCM"})

# Strip types are passed through to rpi_ws281x.ws; validate the identifier
# shape here so a typo fails at startup instead of at the first LED frame.
SUPPORTED_STRIP_TYPES = frozenset(
    {
        "WS2811_STRIP_RGB",
        "WS2811_STRIP_RBW",
        "WS2811_STRIP_GRB",
        "WS2811_STRIP_GBR",
        "WS2811_STRIP_BRG",
        "WS2811_STRIP_BGR",
        "SK6812_STRIP_RGBW",
        "SK6812_STRIP_RBGW",
        "SK6812_STRIP_GRBW",
        "SK6812_STRIP_GBRW",
        "SK6812_STRIP_BRGW",
        "SK6812_STRIP_BGRW",
    }
)


@dataclass(frozen=True)
class LedHardwareSettings:
    enabled: bool
    pin: int
    count: int
    frequency_hz: int
    dma: int
    channel: int
    invert: bool
    strip_type: str
    default_brightness: int
    max_brightness: int
    socket_path: str
    override_timeout_seconds: float
    flow_interval_seconds: float
    flow_color_step: int


@dataclass(frozen=True)
class BuzzerHardwareSettings:
    enabled: bool
    pin: int
    numbering: str
    active_high: bool
    default_duration_seconds: float


@dataclass(frozen=True)
class SerialDeviceSettings:
    port: str
    baudrate: int
    read_timeout_seconds: float
    write_timeout_seconds: float | None = None
    reconnect_seconds: float | None = None


@dataclass(frozen=True)
class StationSettings:
    led: LedHardwareSettings
    buzzer: BuzzerHardwareSettings
    screen: SerialDeviceSettings
    fleet_radio: SerialDeviceSettings


# ---------------------------------------------------------------------------
# Small typed readers.  Every error message includes the JSON path so a bad
# station.local.json is fixed before hardware is touched.
# ---------------------------------------------------------------------------


def _require_object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a JSON object")
    return value


def _require_bool(value: Any, path: str, default: bool | None = None) -> bool:
    if value is None and default is not None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _require_int(
    value: Any,
    path: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{path} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{path} must be <= {maximum}")
    return value


def _require_positive_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a number")
    number = float(value)
    if number <= 0:
        raise ValueError(f"{path} must be > 0")
    return number


def _require_non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _require_numbering(value: Any, path: str) -> str:
    numbering = _require_non_empty_string(value, path)
    if numbering not in SUPPORTED_NUMBERING:
        raise ValueError(
            f"{path} must be one of: {', '.join(sorted(SUPPORTED_NUMBERING))}"
        )
    return numbering


def _require_strip_type(value: Any, path: str) -> str:
    strip_type = _require_non_empty_string(value, path)
    if strip_type not in SUPPORTED_STRIP_TYPES:
        raise ValueError(
            f"{path} must be one of: {', '.join(sorted(SUPPORTED_STRIP_TYPES))}"
        )
    return strip_type


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_led(value: Any) -> LedHardwareSettings:
    data = _require_object(value, "hardware.led")
    settings = LedHardwareSettings(
        enabled=_require_bool(data.get("enabled"), "hardware.led.enabled", True),
        pin=_require_int(data.get("pin"), "hardware.led.pin", minimum=0),
        count=_require_int(data.get("count"), "hardware.led.count", minimum=1),
        frequency_hz=_require_int(
            data.get("frequency_hz"), "hardware.led.frequency_hz", minimum=1
        ),
        dma=_require_int(data.get("dma"), "hardware.led.dma", minimum=0),
        channel=_require_int(data.get("channel"), "hardware.led.channel", minimum=0),
        invert=_require_bool(data.get("invert"), "hardware.led.invert", False),
        strip_type=_require_strip_type(data.get("strip_type"), "hardware.led.strip_type"),
        default_brightness=_require_int(
            data.get("default_brightness"),
            "hardware.led.default_brightness",
            minimum=0,
            maximum=255,
        ),
        max_brightness=_require_int(
            data.get("max_brightness"),
            "hardware.led.max_brightness",
            minimum=0,
            maximum=255,
        ),
        socket_path=_require_non_empty_string(
            data.get("socket_path"), "hardware.led.socket_path"
        ),
        override_timeout_seconds=_require_positive_number(
            data.get("override_timeout_seconds"),
            "hardware.led.override_timeout_seconds",
        ),
        flow_interval_seconds=_require_positive_number(
            data.get("flow_interval_seconds"), "hardware.led.flow_interval_seconds"
        ),
        flow_color_step=_require_int(
            data.get("flow_color_step"), "hardware.led.flow_color_step", minimum=1
        ),
    )
    if settings.default_brightness > settings.max_brightness:
        raise ValueError(
            "hardware.led.default_brightness must not exceed hardware.led.max_brightness"
        )
    return settings


def _parse_buzzer(value: Any) -> BuzzerHardwareSettings:
    data = _require_object(value, "hardware.buzzer")
    return BuzzerHardwareSettings(
        enabled=_require_bool(data.get("enabled"), "hardware.buzzer.enabled", True),
        pin=_require_int(data.get("pin"), "hardware.buzzer.pin", minimum=0),
        numbering=_require_numbering(
            data.get("numbering"), "hardware.buzzer.numbering"
        ),
        active_high=_require_bool(
            data.get("active_high"), "hardware.buzzer.active_high", True
        ),
        default_duration_seconds=_require_positive_number(
            data.get("default_duration_seconds"),
            "hardware.buzzer.default_duration_seconds",
        ),
    )


def _parse_serial_device(value: Any, path: str) -> SerialDeviceSettings:
    data = _require_object(value, path)
    write_timeout = data.get("write_timeout_seconds")
    reconnect = data.get("reconnect_seconds")
    return SerialDeviceSettings(
        port=_require_non_empty_string(data.get("port"), f"{path}.port"),
        baudrate=_require_int(data.get("baudrate"), f"{path}.baudrate", minimum=1),
        read_timeout_seconds=_require_positive_number(
            data.get("read_timeout_seconds"), f"{path}.read_timeout_seconds"
        ),
        write_timeout_seconds=(
            _require_positive_number(write_timeout, f"{path}.write_timeout_seconds")
            if write_timeout is not None
            else None
        ),
        reconnect_seconds=(
            _require_positive_number(reconnect, f"{path}.reconnect_seconds")
            if reconnect is not None
            else None
        ),
    )


def _parse_station(data: Mapping[str, Any]) -> StationSettings:
    hardware = _require_object(data.get("hardware"), "hardware")
    serial = _require_object(data.get("serial"), "serial")
    return StationSettings(
        led=_parse_led(hardware.get("led")),
        buzzer=_parse_buzzer(hardware.get("buzzer")),
        screen=_parse_serial_device(serial.get("screen"), "serial.screen"),
        fleet_radio=_parse_serial_device(serial.get("fleet_radio"), "serial.fleet_radio"),
    )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _default_station_path() -> Path:
    """Return the config/station.local.json path, preferring an existing one."""
    candidates = [
        Path(LOCAL_CONFIG_RELPATH),
        PROJECT_ROOT / LOCAL_CONFIG_RELPATH,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def load_station_settings(path: str | Path | None = None) -> StationSettings:
    """Load and validate the ground-station machine configuration.

    See the module docstring for the lookup order.  Raises ``ValueError`` for
    invalid values (with JSON paths) and ``RuntimeError`` when no station
    configuration can be found.
    """
    config_path: Path | None = None
    if path is not None:
        config_path = Path(path)
    else:
        env_path = os.getenv(STATION_CONFIG_ENV)
        if env_path:
            config_path = Path(env_path)
        else:
            config_path = _default_station_path()

    if not config_path.is_file():
        raise RuntimeError(
            "station configuration not found: "
            f"{config_path}\n"
            "Create it from the template first:\n"
            f"    cp {EXAMPLE_CONFIG_RELPATH} {LOCAL_CONFIG_RELPATH}\n"
            "then edit the serial ports and GPIO pins, or set "
            f"{STATION_CONFIG_ENV} to a station config path."
        )

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read station config {config_path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {config_path}: {exc}") from exc
    return _parse_station(_require_object(data, "config"))
