from __future__ import annotations

import argparse
import time

from components.led_control import GroundLedClient
from components.models import LEDControl, LEDMode


DEFAULT_SCREEN_PORT = (
    "/dev/serial/by-id/usb-ST_Device_STM32_usb_cdc_mode_SANYI_device-if00"
)
WHITE_PIXELS = ((255, 255, 255),) * 7


class StartTokenDetector:
    def __init__(self, token: bytes = b"START"):
        self._token = token.upper()
        self._buffer = bytearray()

    def feed(self, data: bytes) -> bool:
        self._buffer.extend(data.upper())
        if self._token not in self._buffer:
            keep = max(0, len(self._token) - 1)
            if len(self._buffer) > keep:
                del self._buffer[:-keep]
            return False
        self._buffer.clear()
        return True


def run(port: str, baudrate: int, cooldown_seconds: float, log_raw: bool) -> None:
    import serial

    led = GroundLedClient()
    detector = StartTokenDetector()
    white_active = False
    last_toggle_at = 0.0
    serial_obj = serial.Serial()
    serial_obj.port = port
    serial_obj.baudrate = baudrate
    serial_obj.bytesize = serial.EIGHTBITS
    serial_obj.parity = serial.PARITY_NONE
    serial_obj.stopbits = serial.STOPBITS_ONE
    serial_obj.timeout = 0.1
    serial_obj.open()
    print(f"Listening for START on {port} at {baudrate} baud", flush=True)
    try:
        while True:
            data = serial_obj.read(serial_obj.in_waiting or 1)
            if data and log_raw:
                print(f"RX {data.hex(' ')} {data!r}", flush=True)
            if not data or not detector.feed(data):
                continue
            now = time.monotonic()
            if now - last_toggle_at < cooldown_seconds:
                continue
            last_toggle_at = now
            if white_active:
                led.flow()
                white_active = False
                print("START received: flow", flush=True)
            else:
                led.apply(LEDControl(LEDMode.PIXELS, brightness=4, pixels=WHITE_PIXELS))
                white_active = True
                print("START received: white", flush=True)
    finally:
        serial_obj.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Toggle GPIO18 LEDs from screen START messages")
    parser.add_argument("--port", default=DEFAULT_SCREEN_PORT)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--cooldown", type=float, default=0.35)
    parser.add_argument("--log-raw", action="store_true")
    args = parser.parse_args()
    run(args.port, args.baud, args.cooldown, args.log_raw)


if __name__ == "__main__":
    main()
