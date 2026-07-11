#!/home/cooper/.venvs/ws281x/bin/python
"""WS2812 owner process: default flow, temporary aircraft-controlled pixels."""

import os
import select
import signal
import socket
import time

from rpi_ws281x import Color, PixelStrip, ws


LED_COUNT = 7
LED_PIN = 18
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_CHANNEL = 0
DEFAULT_BRIGHTNESS = 3
OVERRIDE_TIMEOUT_SECONDS = 30.0
SOCKET_PATH = "/run/ground-station-led.sock"
FLOW_COLORS = ((255, 0, 0), (255, 120, 0), (0, 255, 0), (0, 180, 255), (80, 0, 255))

running = True


def request_stop(_signal=None, _frame=None):
    global running
    running = False


def set_pixels(strip, pixels, brightness):
    strip.setBrightness(max(0, min(20, brightness)))
    for index, (red, green, blue) in enumerate(pixels):
        strip.setPixelColor(index, Color(red, green, blue))
    strip.show()


def flow_pixels(step):
    base = FLOW_COLORS[(step // LED_COUNT) % len(FLOW_COLORS)]
    head = step % LED_COUNT
    pixels = [(0, 0, 0)] * LED_COUNT
    for offset, scale in ((0, 1.0), (-1, 0.35), (-2, 0.12)):
        pixels[(head + offset) % LED_COUNT] = tuple(int(value * scale) for value in base)
    return pixels


def parse_control(data):
    if len(data) < 3:
        return None
    mode, brightness, count = data[:3]
    if mode == 0 and count == 0 and len(data) == 3:
        return None
    if mode != 1 or count != LED_COUNT or len(data) != 3 + LED_COUNT * 3:
        return False
    return brightness, tuple(
        tuple(data[index : index + 3]) for index in range(3, len(data), 3)
    )


def main():
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
    override = None
    override_at = 0.0
    step = 0
    next_flow = 0.0
    try:
        while running:
            readable, _, _ = select.select((server,), (), (), 0.05)
            if readable:
                control = parse_control(server.recv(64))
                if control is None:
                    override = None
                elif control is not False:
                    override = control
                    override_at = time.monotonic()
                    set_pixels(strip, control[1], control[0])
            now = time.monotonic()
            if override is not None and now - override_at >= OVERRIDE_TIMEOUT_SECONDS:
                override = None
            if override is None and now >= next_flow:
                set_pixels(strip, flow_pixels(step), DEFAULT_BRIGHTNESS)
                step += 1
                next_flow = now + 0.16
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
