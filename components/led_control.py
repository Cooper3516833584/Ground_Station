from __future__ import annotations

import json
import socket
from typing import Iterable

from .models import LEDControl

# Fallback defaults for callers that do not load the station configuration.
# Entry programs always build clients from station settings via
# ``GroundLedClient.from_settings`` or ``build_ground_led``.
DEFAULT_LED_SOCKET = "/run/ground-station-led.sock"
DEFAULT_LED_COUNT = 7
LED_CONTROL_PREFIX = b"GSLED1:"
UNIX_SOCKET_FAMILY = getattr(socket, "AF_UNIX", 1)


def _rgb(value: Iterable[int]) -> tuple[int, int, int]:
    color = tuple(value)
    if len(color) != 3 or not all(
        isinstance(channel, int) and not isinstance(channel, bool) and 0 <= channel <= 255
        for channel in color
    ):
        raise ValueError("LED color must contain three integers between 0 and 255")
    return color


def _brightness(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise ValueError("LED brightness must be between 0 and 255")
    return value


class GroundLedClient:
    """Single-call control client for the boot-persistent GPIO LED daemon.

    The socket path and the expected pixel count come from the station
    configuration (``hardware.led``); this class only keeps safe fallback
    defaults so it stays usable without configuration in tests and tools.
    """

    def __init__(
        self,
        socket_path: str = DEFAULT_LED_SOCKET,
        pixel_count: int = DEFAULT_LED_COUNT,
        default_brightness: int = 3,
        flow_interval_seconds: float = 0.16,
    ):
        self._socket_path = socket_path
        self._pixel_count = pixel_count
        self._default_brightness = default_brightness
        self._flow_interval_seconds = flow_interval_seconds

    @classmethod
    def from_settings(cls, led_settings) -> "GroundLedClient":
        """Build a client from ``StationSettings.led`` (duck-typed)."""
        return cls(
            socket_path=led_settings.socket_path,
            pixel_count=led_settings.count,
            default_brightness=led_settings.default_brightness,
            flow_interval_seconds=led_settings.flow_interval_seconds,
        )

    @property
    def pixel_count(self) -> int:
        return self._pixel_count

    def apply(self, control: LEDControl) -> None:
        """Apply the existing aircraft LED_CONTROL payload without changing its format."""
        with socket.socket(UNIX_SOCKET_FAMILY, socket.SOCK_DGRAM) as client:
            client.sendto(control.to_payload(), self._socket_path)

    def set(
        self,
        *,
        mode: str,
        color: Iterable[int] = (0, 0, 0),
        brightness: int = 3,
        interval_seconds: float = 0.5,
        pixels: Iterable[Iterable[int]] | None = None,
    ) -> None:
        """Set an indefinite local mode: off, solid, blink, flow, or pixels."""
        if mode not in {"off", "solid", "blink", "flow", "pixels"}:
            raise ValueError("LED mode must be off, solid, blink, flow, or pixels")
        if isinstance(interval_seconds, bool) or not isinstance(
            interval_seconds, (int, float)
        ):
            raise ValueError("LED interval_seconds must be a number")
        interval = float(interval_seconds)
        if not 0.05 <= interval <= 60.0:
            raise ValueError("LED interval_seconds must be between 0.05 and 60")
        message = {
            "mode": mode,
            "color": list(_rgb(color)),
            "brightness": _brightness(brightness),
            "interval_seconds": interval,
        }
        if pixels is not None:
            pixel_values = [list(_rgb(pixel)) for pixel in pixels]
            if len(pixel_values) != self._pixel_count:
                raise ValueError(
                    f"pixels mode requires exactly {self._pixel_count} RGB values"
                )
            message["pixels"] = pixel_values
        elif mode == "pixels":
            raise ValueError(
                f"pixels mode requires exactly {self._pixel_count} RGB values"
            )
        payload = LED_CONTROL_PREFIX + json.dumps(
            message, separators=(",", ":")
        ).encode("ascii")
        with socket.socket(UNIX_SOCKET_FAMILY, socket.SOCK_DGRAM) as client:
            client.sendto(payload, self._socket_path)

    def solid(self, color: Iterable[int], brightness: int = 3) -> None:
        self.set(mode="solid", color=color, brightness=brightness)

    def blink(
        self,
        color: Iterable[int],
        brightness: int = 3,
        interval_seconds: float = 0.5,
    ) -> None:
        self.set(
            mode="blink",
            color=color,
            brightness=brightness,
            interval_seconds=interval_seconds,
        )

    def pixels(self, values: Iterable[Iterable[int]], brightness: int = 3) -> None:
        self.set(mode="pixels", brightness=brightness, pixels=values)

    def flow(
        self,
        brightness: int | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        """Restore the flow pattern.

        Without explicit arguments, clients built from station settings use
        ``hardware.led.default_brightness`` and
        ``hardware.led.flow_interval_seconds``; a plain ``GroundLedClient()``
        keeps the historical defaults (brightness 3, interval 0.16).
        """
        self.set(
            mode="flow",
            brightness=(
                self._default_brightness if brightness is None else brightness
            ),
            interval_seconds=(
                self._flow_interval_seconds
                if interval_seconds is None
                else interval_seconds
            ),
        )

    def off(self) -> None:
        self.set(mode="off", brightness=0)


def build_ground_led(station) -> GroundLedClient:
    """Build the ground LED client from a loaded ``StationSettings``."""
    return GroundLedClient.from_settings(station.led)


def set_led(
    *,
    mode: str,
    color: Iterable[int] = (0, 0, 0),
    brightness: int = 3,
    interval_seconds: float = 0.5,
    pixels: Iterable[Iterable[int]] | None = None,
    socket_path: str = DEFAULT_LED_SOCKET,
    pixel_count: int = DEFAULT_LED_COUNT,
) -> None:
    """Control all LEDs with one function call."""
    GroundLedClient(socket_path, pixel_count=pixel_count).set(
        mode=mode,
        color=color,
        brightness=brightness,
        interval_seconds=interval_seconds,
        pixels=pixels,
    )
