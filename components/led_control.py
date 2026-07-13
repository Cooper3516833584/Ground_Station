from __future__ import annotations

import socket

from .models import LEDControl, LEDMode


DEFAULT_LED_SOCKET = "/run/ground-station-led.sock"
BLACK_PIXELS = ((0, 0, 0),) * 7


class GroundLedClient:
    """Client for the boot-persistent GPIO18 LED daemon."""

    def __init__(self, socket_path: str = DEFAULT_LED_SOCKET):
        self._socket_path = socket_path

    def apply(self, control: LEDControl) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.sendto(control.to_payload(), self._socket_path)

    def flow(self, brightness: int = 3) -> None:
        self.apply(LEDControl(LEDMode.FLOW, brightness))

    def off(self) -> None:
        self.apply(LEDControl(LEDMode.PIXELS, brightness=0, pixels=BLACK_PIXELS))
