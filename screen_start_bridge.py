from __future__ import annotations

import argparse
from pathlib import Path
import threading
import time

from components.config import load_hmac_key
from components.ground_link import GroundStationLink
from components.led_control import GroundLedClient
from components.models import (
    AckStatus,
    Command,
    CommandAck,
    CommandId,
    FCState,
    LEDControl,
    LEDMode,
    MissionState,
    MissionStatus,
)


DEFAULT_SCREEN_PORT = (
    "/dev/serial/by-id/usb-jixin.pro_CMSIS-DAP_LU_LU_2022_8888-if00"
)
DEFAULT_SCREEN_BAUD = 9600
DEFAULT_HC14_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
WHITE_PIXELS = ((255, 255, 255),) * 7
WHITE_BRIGHTNESS = 4
WHITE_REFRESH_SECONDS = 10.0


class StartTokenDetector:
    """Detect case-insensitive START tokens, including tokens split across reads."""

    def __init__(self, token: bytes = b"START"):
        self._token = bytes(token).upper()
        self._buffer = bytearray()

    def feed(self, data: bytes) -> int:
        if not data:
            return 0
        self._buffer.extend(data.upper())
        count = 0
        while True:
            index = self._buffer.find(self._token)
            if index < 0:
                keep = max(0, len(self._token) - 1)
                if len(self._buffer) > keep:
                    del self._buffer[:-keep]
                return count
            count += 1
            del self._buffer[: index + len(self._token)]


class ScreenStartBridge:
    def __init__(
        self,
        *,
        hc14_port: str,
        hc14_baudrate: int,
        hmac_key: bytes,
        cooldown_seconds: float,
    ):
        self._led = GroundLedClient()
        self._cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._start_in_progress = False
        self._mission_acknowledged = False
        self._seen_unlocked = False
        self._command_seq: int | None = None
        self._last_button_at = 0.0
        self._last_white_at = 0.0
        self.link = GroundStationLink(
            port=hc14_port,
            baudrate=hc14_baudrate,
            key=hmac_key,
            on_fc_state=self._on_fc_state,
            on_mission_status=self._on_mission_status,
            on_ack=self._on_ack,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
        )

    def start(self) -> None:
        self._set_flow("program ready")
        self.link.start()

    def close(self) -> None:
        self.link.close()

    def handle_screen_start(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_button_at < self._cooldown_seconds:
                print("Screen START ignored by cooldown", flush=True)
                return
            self._last_button_at = now
            if self._start_in_progress:
                print("Screen START ignored: mission already pending/running", flush=True)
                return
            if not self.link.connected:
                print("Screen START ignored: HC-14 is not connected", flush=True)
                return
            self._start_in_progress = True
            self._mission_acknowledged = False
            self._seen_unlocked = False
            self._command_seq = None

        self._set_white("screen START")
        self.link.enable_preflight_commands()
        try:
            seq = self.link.send_command(Command(CommandId.START_MISSION))
        except RuntimeError as exc:
            print(f"START_MISSION send failed: {exc}", flush=True)
            self._finish("send failed")
            return

        with self._lock:
            self._command_seq = seq
        print(f"START_MISSION sent through HC-14 (seq={seq})", flush=True)

    def tick(self) -> None:
        try:
            self.link.poll()
        except RuntimeError:
            pass

        with self._lock:
            active = self._start_in_progress
            acknowledged = self._mission_acknowledged
            seq = self._command_seq
            refresh_white = (
                active
                and time.monotonic() - self._last_white_at >= WHITE_REFRESH_SECONDS
            )

        if refresh_white:
            self._set_white("mission active refresh")

        if active and not acknowledged and seq is not None:
            pending = self.link.pending_for_seq(seq)
            if pending is not None and pending.done and pending.failed_reason:
                self._finish(f"START_MISSION {pending.failed_reason}")

    def _on_connected(self) -> None:
        print("HC-14 connected", flush=True)

    def _on_disconnected(self, exc: Exception | None) -> None:
        detail = str(exc) if exc else "link disconnected"
        print(f"HC-14 disconnected: {detail}", flush=True)
        with self._lock:
            should_finish = (
                self._start_in_progress and not self._mission_acknowledged
            )
        if should_finish:
            self._finish("link lost before mission acceptance")

    def _on_ack(self, ack: CommandAck, _session: int) -> None:
        if ack.command_id != CommandId.START_MISSION:
            return
        print(
            f"Aircraft ACK: {ack.status.name} reason={ack.reason.name} seq={ack.seq}",
            flush=True,
        )
        if ack.status == AckStatus.RECEIVED:
            return
        if ack.status == AckStatus.ACCEPTED:
            with self._lock:
                if self._start_in_progress:
                    self._mission_acknowledged = True
            self.link.disable_commands_for_flight()
            return
        if ack.status in (
            AckStatus.REJECTED,
            AckStatus.COMPLETED,
            AckStatus.FAILED,
        ):
            self._finish(f"aircraft ACK {ack.status.name}")

    def _on_mission_status(self, status: MissionStatus, _session: int) -> None:
        print(
            f"Mission status: {status.state.name} progress={status.progress}% "
            f"message={status.message!r}",
            flush=True,
        )
        if status.state in (MissionState.COMPLETED, MissionState.FAILED):
            self._finish(f"mission {status.state.name}")

    def _on_fc_state(self, state: FCState, _session: int) -> None:
        landed_after_flight = False
        with self._lock:
            if not self._start_in_progress:
                return
            if state.unlock:
                self._seen_unlocked = True
            elif self._seen_unlocked:
                landed_after_flight = True
        if landed_after_flight:
            self._finish("aircraft landed and locked")

    def _finish(self, reason: str) -> None:
        with self._lock:
            if not self._start_in_progress:
                return
            self._start_in_progress = False
            self._mission_acknowledged = False
            self._seen_unlocked = False
            self._command_seq = None
        self.link.enable_preflight_commands()
        self._set_flow(reason)

    def _set_white(self, reason: str) -> None:
        try:
            self._led.apply(
                LEDControl(
                    LEDMode.PIXELS,
                    brightness=WHITE_BRIGHTNESS,
                    pixels=WHITE_PIXELS,
                )
            )
            with self._lock:
                self._last_white_at = time.monotonic()
            print(f"LED -> white ({reason})", flush=True)
        except OSError as exc:
            print(f"LED white unavailable: {exc}", flush=True)

    def _set_flow(self, reason: str) -> None:
        try:
            self._led.flow()
            print(f"LED -> flow ({reason})", flush=True)
        except OSError as exc:
            print(f"LED flow unavailable: {exc}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Forward screen START to the aircraft and control mission LEDs"
    )
    parser.add_argument("--screen-port", default=DEFAULT_SCREEN_PORT)
    parser.add_argument("--screen-baud", type=int, default=DEFAULT_SCREEN_BAUD)
    parser.add_argument("--hc14-port", default=DEFAULT_HC14_PORT)
    parser.add_argument("--hc14-baud", type=int, default=115200)
    parser.add_argument("--cooldown", type=float, default=0.75)
    parser.add_argument(
        "--hmac-key-file",
        default=str(Path(__file__).resolve().parent / "config" / "secrets" / "hmac.key"),
    )
    parser.add_argument("--log-raw", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    import serial

    hmac_key = load_hmac_key(key_file=args.hmac_key_file)
    bridge = ScreenStartBridge(
        hc14_port=args.hc14_port,
        hc14_baudrate=args.hc14_baud,
        hmac_key=hmac_key,
        cooldown_seconds=max(0.0, args.cooldown),
    )
    detector = StartTokenDetector()
    screen = serial.Serial()
    screen.port = args.screen_port
    screen.baudrate = args.screen_baud
    screen.bytesize = serial.EIGHTBITS
    screen.parity = serial.PARITY_NONE
    screen.stopbits = serial.STOPBITS_ONE
    screen.timeout = 0.05

    bridge.start()
    try:
        screen.open()
        print(
            f"Listening for screen START on {args.screen_port} @ {args.screen_baud}; "
            f"HC-14 {args.hc14_port} @ {args.hc14_baud}",
            flush=True,
        )
        while True:
            data = screen.read(screen.in_waiting or 1)
            if data and args.log_raw:
                print(f"SCREEN RX {data.hex(' ')} {data!r}", flush=True)
            for _ in range(detector.feed(data)):
                bridge.handle_screen_start()
            bridge.tick()
    except KeyboardInterrupt:
        print("Stopping screen START bridge", flush=True)
    finally:
        if screen.is_open:
            screen.close()
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
