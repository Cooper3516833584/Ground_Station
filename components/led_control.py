from __future__ import annotations

import json
import socket
from typing import Iterable

from .models import LEDControl


DEFAULT_LED_SOCKET = "/run/ground-station-led.sock"
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
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 20:
        raise ValueError("LED brightness must be between 0 and 20")
    return value


class GroundLedClient:
    """Single-call control client for the boot-persistent GPIO18 LED daemon."""

    def __init__(self, socket_path: str = DEFAULT_LED_SOCKET):
        self._socket_path = socket_path

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
            if len(pixel_values) != 7:
                raise ValueError("pixels mode requires exactly 7 RGB values")
            message["pixels"] = pixel_values
        elif mode == "pixels":
            raise ValueError("pixels mode requires exactly 7 RGB values")
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

    def flow(self, brightness: int = 3, interval_seconds: float = 0.16) -> None:
        self.set(
            mode="flow",
            brightness=brightness,
            interval_seconds=interval_seconds,
        )

    def off(self) -> None:
        self.set(mode="off", brightness=0)


def set_led(
    *,
    mode: str,
    color: Iterable[int] = (0, 0, 0),
    brightness: int = 3,
    interval_seconds: float = 0.5,
    pixels: Iterable[Iterable[int]] | None = None,
    socket_path: str = DEFAULT_LED_SOCKET,
) -> None:
    """Control all seven LEDs with one function call."""
    GroundLedClient(socket_path).set(
        mode=mode,
        color=color,
        brightness=brightness,
        interval_seconds=interval_seconds,
        pixels=pixels,
    )
