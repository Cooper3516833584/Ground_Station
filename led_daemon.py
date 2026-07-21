#!/home/cooper/.venvs/ws281x/bin/python
"""WS2812 owner process for persistent local modes and aircraft LED commands."""

import json
import os
import select
import signal
import socket
import time

LED_COUNT = 7
LED_PIN = 18
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_CHANNEL = 0
DEFAULT_BRIGHTNESS = 3
OVERRIDE_TIMEOUT_SECONDS = 30.0
SOCKET_PATH = "/run/ground-station-led.sock"
CONTROL_PREFIX = b"GSLED1:"
FLOW_COLOR_STEP = 3

running = True


def request_stop(_signal=None, _frame=None):
    global running
    running = False


def set_pixels(strip, pixels, brightness, color_factory):
    strip.setBrightness(max(0, min(20, brightness)))
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


def flow_pixels(step):
    """Move one illuminated pixel while continuously shifting its color."""
    pixels = [(0, 0, 0)] * LED_COUNT
    pixels[step % LED_COUNT] = color_wheel(step * FLOW_COLOR_STEP)
    return tuple(pixels)


def parse_control(data):
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
                or not 0 <= brightness <= 20
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
                if len(pixels) != LED_COUNT or not all(
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
    mode, brightness, count = data[:3]
    if mode == 0 and count == 0 and len(data) == 3:
        return {
            "mode": "flow",
            "brightness": brightness,
            "interval_seconds": 0.16,
            "color": (0, 0, 0),
            "pixels": None,
            "expires_at": None,
        }
    if mode != 1 or count != LED_COUNT or len(data) != 3 + LED_COUNT * 3:
        return False
    return {
        "mode": "pixels",
        "brightness": brightness,
        "interval_seconds": 0.5,
        "color": (0, 0, 0),
        "pixels": tuple(
            tuple(data[index : index + 3]) for index in range(3, len(data), 3)
        ),
        "expires_at": time.monotonic() + OVERRIDE_TIMEOUT_SECONDS,
    }


def render_pattern(strip, pattern, step, color_factory):
    mode = pattern["mode"]
    if mode == "off":
        pixels = ((0, 0, 0),) * LED_COUNT
    elif mode == "solid":
        pixels = (pattern["color"],) * LED_COUNT
    elif mode == "blink":
        pixels = (
            (pattern["color"],) * LED_COUNT
            if step % 2 == 0
            else ((0, 0, 0),) * LED_COUNT
        )
    elif mode == "pixels":
        pixels = pattern["pixels"]
    else:
        pixels = flow_pixels(step)
    set_pixels(strip, pixels, pattern["brightness"], color_factory)


def main():
    from rpi_ws281x import Color, PixelStrip, ws

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)
    strip = PixelStrip(
        LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, False,
        DEFAULT_BRIGHTNESS, LED_CHANNEL, ws.WS2811_STRIP_GRB,
    )
    strip.begin()
    pattern = {
        "mode": "flow",
        "brightness": DEFAULT_BRIGHTNESS,
        "interval_seconds": 0.16,
        "color": (0, 0, 0),
        "pixels": None,
        "expires_at": None,
    }
    step = 0
    next_frame = 0.0
    try:
        while running:
            readable, _, _ = select.select((server,), (), (), 0.05)
            if readable:
                control = parse_control(server.recv(1024))
                if control is not False:
                    pattern = control
                    step = 0
                    next_frame = 0.0
            now = time.monotonic()
            expires_at = pattern["expires_at"]
            if expires_at is not None and now >= expires_at:
                pattern = {
                    "mode": "flow",
                    "brightness": DEFAULT_BRIGHTNESS,
                    "interval_seconds": 0.16,
                    "color": (0, 0, 0),
                    "pixels": None,
                    "expires_at": None,
                }
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
        for index in range(LED_COUNT):
            strip.setPixelColor(index, Color(0, 0, 0))
        strip.show()
        server.close()
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
