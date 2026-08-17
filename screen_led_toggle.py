from __future__ import annotations

import argparse
import time

from components.led_control import GroundLedClient
from components.models import LEDControl, LEDMode, configure_led_pixel_count
from components.station_config import load_station_settings


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


def white_pixels_for_count(count: int) -> tuple[tuple[int, int, int], ...]:
    return ((255, 255, 255),) * count


def run(
    port: str,
    baudrate: int,
    cooldown_seconds: float,
    log_raw: bool,
    station=None,
) -> None:
    import serial

    configure_led_pixel_count(station.led.count)
    led = GroundLedClient.from_settings(station.led)
    detector = StartTokenDetector()
    white_active = False
    last_toggle_at = 0.0
    serial_obj = serial.Serial()
    serial_obj.port = port
    serial_obj.baudrate = baudrate
    serial_obj.bytesize = serial.EIGHTBITS
    serial_obj.parity = serial.PARITY_NONE
    serial_obj.stopbits = serial.STOPBITS_ONE
    serial_obj.timeout = station.screen.read_timeout_seconds
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
                led.apply(
                    LEDControl(
                        LEDMode.PIXELS,
                        brightness=4,
                        pixels=white_pixels_for_count(station.led.count),
                    )
                )
                white_active = True
                print("START received: white", flush=True)
    finally:
        serial_obj.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Toggle ground-station LEDs from screen START messages"
    )
    parser.add_argument(
        "--station-config",
        default=None,
        help="path to the station machine configuration "
        "(default: GROUND_STATION_CONFIG or config/station.local.json)",
    )
    parser.add_argument("--port", default=None)
    parser.add_argument("--baud", type=int, default=None)
    parser.add_argument("--cooldown", type=float, default=0.35)
    parser.add_argument("--log-raw", action="store_true")
    args = parser.parse_args()
    station = load_station_settings(args.station_config)
    port = args.port or station.screen.port
    baudrate = args.baud or station.screen.baudrate
    run(port, baudrate, args.cooldown, args.log_raw, station=station)


if __name__ == "__main__":
    main()
