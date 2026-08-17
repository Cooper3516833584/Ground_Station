#!/usr/bin/env python3
"""WS2812 owner process for persistent local modes and aircraft LED commands.

This daemon is the *only* process that may touch the WS2812 GPIO.  Other
programs talk to it over a Unix datagram socket (``GSLED1:`` JSON control
messages, plus the legacy binary LED_CONTROL payload).  All hardware settings
come from the station configuration (``config/station.local.json`` or
``--station-config``).
"""

import argparse
import json
import os
import select
import signal
import socket
import sys
import time

from components.station_config import load_station_settings

CONTROL_PREFIX = b"GSLED1:"

# Fallback used by the pattern helpers when no station configuration has been
# loaded (e.g. unit tests).  ``main()`` replaces these from
# ``hardware.led`` before opening the socket.
led_count = 7
FLOW_COLOR_STEP = 3
# Machine-side safety cap: the brightness actually written to the strip never
# exceeds hardware.led.max_brightness.  Protocol messages may still carry any
# 0..255 value; the daemon clamps at render time.  ``main()`` sets this from
# the station configuration.
max_brightness = 255

running = True


def request_stop(_signal=None, _frame=None):
    global running
    running = False


def configure_led_count(count: int) -> None:
    """Set the LED count used by the pattern helpers (from station config)."""
    global led_count
    led_count = count


def configure_max_brightness(value: int) -> None:
    """Set the machine-side brightness cap (hardware.led.max_brightness)."""
    global max_brightness
    max_brightness = max(0, min(255, value))


def set_pixels(strip, pixels, brightness, color_factory):
    strip.setBrightness(max(0, min(255, brightness)))
    for index, (red, green, blue) in enumerate(pixels):
        strip.setPixelColor(index, color_factory(red, green, blue))
    strip.show()


def color_wheel(position):
    """Return a smooth RGB color from a 256-step circular color wheel."""
    position %= 256
    if position < 85:
        return (255 - position * 3, position * 3, 0)
    if position < 170:
        position -= 85
        return (0, 255 - position * 3, position * 3)
    position -= 170
    return (position * 3, 0, 255 - position * 3)


def flow_pixels(step, *, count: int | None = None, color_step: int = 3):
    """Move one illuminated pixel while continuously shifting its color."""
    pixel_count = count if count is not None else led_count
    pixels = [(0, 0, 0)] * pixel_count
    pixels[step % pixel_count] = color_wheel(step * color_step)
    return tuple(pixels)


def parse_control(data, *, count: int | None = None, override_timeout: float = 30.0):
    """Parse a control datagram into a pattern dict, or ``False`` when invalid."""
    pixel_count = count if count is not None else led_count
    if data.startswith(CONTROL_PREFIX):
        try:
            control = json.loads(data[len(CONTROL_PREFIX) :].decode("ascii"))
            mode = control["mode"]
            brightness = control["brightness"]
            interval = float(control["interval_seconds"])
            color = tuple(control["color"])
            if mode not in {"off", "solid", "blink", "flow", "pixels"}:
                return False
            if (
                isinstance(brightness, bool)
                or not isinstance(brightness, int)
                or not 0 <= brightness <= 255
                or not 0.05 <= interval <= 60.0
                or len(color) != 3
                or not all(
                    isinstance(channel, int)
                    and not isinstance(channel, bool)
                    and 0 <= channel <= 255
                    for channel in color
                )
            ):
                return False
            pixels = None
            if mode == "pixels":
                pixels = tuple(tuple(pixel) for pixel in control["pixels"])
                if len(pixels) != pixel_count or not all(
                    len(pixel) == 3
                    and all(
                        isinstance(channel, int)
                        and not isinstance(channel, bool)
                        and 0 <= channel <= 255
                        for channel in pixel
                    )
                    for pixel in pixels
                ):
                    return False
            return {
                "mode": mode,
                "brightness": brightness,
                "interval_seconds": interval,
                "color": color,
                "pixels": pixels,
                "expires_at": None,
            }
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return False
    if len(data) < 3:
        return False
    mode, brightness, count_byte = data[:3]
    if mode == 0 and count_byte == 0 and len(data) == 3:
        return {
            "mode": "flow",
            "brightness": brightness,
            "interval_seconds": 0.16,
            "color": (0, 0, 0),
            "pixels": None,
            "expires_at": None,
        }
    if mode != 1 or count_byte != pixel_count or len(data) != 3 + pixel_count * 3:
        return False
    return {
        "mode": "pixels",
        "brightness": brightness,
        "interval_seconds": 0.5,
        "color": (0, 0, 0),
        "pixels": tuple(
            tuple(data[index : index + 3]) for index in range(3, len(data), 3)
        ),
        "expires_at": time.monotonic() + override_timeout,
    }


def render_pattern(strip, pattern, step, color_factory):
    mode = pattern["mode"]
    if mode == "off":
        pixels = ((0, 0, 0),) * led_count
    elif mode == "solid":
        pixels = (pattern["color"],) * led_count
    elif mode == "blink":
        pixels = (
            (pattern["color"],) * led_count
            if step % 2 == 0
            else ((0, 0, 0),) * led_count
        )
    elif mode == "pixels":
        pixels = pattern["pixels"]
    else:
        pixels = flow_pixels(step, count=led_count, color_step=FLOW_COLOR_STEP)
    effective_brightness = min(pattern["brightness"], max_brightness)
    set_pixels(strip, pixels, effective_brightness, color_factory)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="WS2812 owner daemon; all hardware settings come from the station config"
    )
    parser.add_argument(
        "--station-config",
        default=None,
        help="path to the station machine configuration "
        "(default: GROUND_STATION_CONFIG or config/station.local.json)",
    )
    return parser.parse_args()


def build_idle_pattern(led) -> dict:
    return {
        "mode": "flow",
        "brightness": led.default_brightness,
        "interval_seconds": led.flow_interval_seconds,
        "color": (0, 0, 0),
        "pixels": None,
        "expires_at": None,
    }


def main() -> int:
    args = parse_args()
    station = load_station_settings(args.station_config)
    led = station.led
    if not led.enabled:
        print(
            "hardware.led.enabled is false; LED daemon has nothing to do and exits.",
            flush=True,
        )
        return 0

    configure_led_count(led.count)
    global FLOW_COLOR_STEP
    FLOW_COLOR_STEP = led.flow_color_step
    configure_max_brightness(led.max_brightness)

    from rpi_ws281x import Color, PixelStrip, ws

    if not hasattr(ws, led.strip_type):
        print(
            f"hardware.led.strip_type {led.strip_type!r} is not defined by "
            "rpi_ws281x.ws; update the station config.",
            flush=True,
        )
        return 2

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        os.unlink(led.socket_path)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(led.socket_path)
    os.chmod(led.socket_path, 0o666)
    strip = PixelStrip(
        led.count, led.pin, led.frequency_hz, led.dma, led.invert,
        led.default_brightness, led.channel, getattr(ws, led.strip_type),
    )
    strip.begin()
    pattern = build_idle_pattern(led)
    step = 0
    next_frame = 0.0
    try:
        while running:
            readable, _, _ = select.select((server,), (), (), 0.05)
            if readable:
                control = parse_control(
                    server.recv(1024),
                    count=led.count,
                    override_timeout=led.override_timeout_seconds,
                )
                if control is not False:
                    pattern = control
                    step = 0
                    next_frame = 0.0
            now = time.monotonic()
            expires_at = pattern["expires_at"]
            if expires_at is not None and now >= expires_at:
                pattern = build_idle_pattern(led)
                step = 0
                next_frame = 0.0
            if now >= next_frame:
                render_pattern(strip, pattern, step, Color)
                step += 1
                if pattern["mode"] in {"off", "solid", "pixels"}:
                    next_frame = float("inf")
                else:
                    next_frame = now + pattern["interval_seconds"]
    finally:
        for index in range(led.count):
            strip.setPixelColor(index, Color(0, 0, 0))
        strip.show()
        server.close()
        try:
            os.unlink(led.socket_path)
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
