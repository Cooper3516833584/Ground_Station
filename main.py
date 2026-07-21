from __future__ import annotations

import argparse
from pathlib import Path
import time

from components.config import load_hmac_key
from components.ground_link import GroundStationLink
from components.led_control import GroundLedClient
from components.models import (
    AckStatus,
    CommandAck,
    FCState,
    LEDControl,
    MissionState,
    MissionStatus,
)
from components.screen_commands import ScreenCommandDetector
from components.task_config import LedSettings, ScreenAction, TaskSettings, load_task_settings


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT / "task_config.json"
DEFAULT_KEY_PATH = ROOT / "config" / "secrets" / "hmac.key"


def apply_led_settings(client: GroundLedClient, settings: LedSettings) -> None:
    """The only helper normally needed to control LED color, brightness, and mode."""
    client.set(
        mode=settings.mode,
        color=settings.color,
        brightness=settings.brightness,
        interval_seconds=settings.interval_seconds,
    )


class TaskRuntime:
    """Reusable task shell: screen input -> configured authenticated aircraft command."""

    def __init__(self, settings: TaskSettings, hmac_key: bytes):
        self.settings = settings
        self.led = GroundLedClient()
        self._last_action_at: dict[str, float] = {}
        self._actions = {action.token.upper(): action for action in settings.actions}
        self.link = GroundStationLink(
            port=settings.serial.hc14_port,
            baudrate=settings.serial.hc14_baudrate,
            key=hmac_key,
            on_fc_state=self._on_fc_state,
            on_mission_status=self._on_mission_status,
            on_ack=self._on_ack,
            on_led_control=self._on_aircraft_led,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
        )

    def start(self) -> None:
        # Replace the boot flow immediately while keeping the daemon alive as GPIO owner.
        try:
            self.led.off()
            apply_led_settings(self.led, self.settings.startup_led)
        except OSError as exc:
            print(f"LED unavailable: {exc}", flush=True)
        self.link.start()

    def close(self) -> None:
        self.link.close()
        try:
            self.led.off()
        except OSError as exc:
            print(f"LED shutdown unavailable: {exc}", flush=True)

    def handle_screen_token(self, token: str) -> None:
        action = self._actions[token.upper()]
        now = time.monotonic()
        last_at = self._last_action_at.get(action.token.upper(), 0.0)
        if now - last_at < self.settings.serial.cooldown_seconds:
            print(f"Screen {action.token!r} ignored by cooldown", flush=True)
            return
        self._last_action_at[action.token.upper()] = now
        if not self.link.connected:
            print(f"Screen {action.token!r} ignored: HC-14 is not connected", flush=True)
            return
        try:
            seq = self.link.send_command(action.command)
        except RuntimeError as exc:
            print(
                f"{action.command.command_id.name} send failed for {action.token!r}: {exc}",
                flush=True,
            )
            return
        print(
            f"Screen {action.token!r} -> {action.command.command_id.name} (seq={seq})",
            flush=True,
        )
        self.on_command_sent(action, seq)

    def on_command_sent(self, action: ScreenAction, _seq: int) -> None:
        """Task template hook: add lightweight post-send behavior here if needed."""
        if action.led is None:
            return
        try:
            apply_led_settings(self.led, action.led)
        except OSError as exc:
            print(f"LED unavailable: {exc}", flush=True)

    def tick(self) -> None:
        try:
            self.link.poll()
        except RuntimeError as exc:
            print(f"HC-14 poll failed: {exc}", flush=True)

    def _on_connected(self) -> None:
        print("HC-14 connected", flush=True)

    def _on_disconnected(self, exc: Exception | None) -> None:
        detail = str(exc) if exc else "link disconnected"
        print(f"HC-14 disconnected: {detail}", flush=True)

    def _on_fc_state(self, _state: FCState, _session: int) -> None:
        self.link.disable_commands_for_flight()

    def _on_mission_status(self, status: MissionStatus, _session: int) -> None:
        print(
            f"Mission {status.state.name}: {status.progress}% {status.message}",
            flush=True,
        )
        if status.state in {
            MissionState.IDLE,
            MissionState.READY,
            MissionState.COMPLETED,
            MissionState.FAILED,
        }:
            self.link.enable_preflight_commands()

    def _on_ack(self, ack: CommandAck, _session: int) -> None:
        print(
            f"Aircraft ACK {ack.command_id.name}: {ack.status.name} "
            f"reason={ack.reason.name} seq={ack.seq}",
            flush=True,
        )
        if ack.status in {AckStatus.REJECTED, AckStatus.FAILED}:
            try:
                self.led.blink((255, 0, 0), brightness=4, interval_seconds=0.25)
            except OSError as exc:
                print(f"LED unavailable: {exc}", flush=True)

    def _on_aircraft_led(self, control: LEDControl, _session: int) -> None:
        try:
            self.led.apply(control)
        except OSError as exc:
            print(f"Aircraft LED command unavailable: {exc}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configurable screen-to-aircraft ground-station task template"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--task", help="override active_task in task_config.json")
    parser.add_argument("--hmac-key-file", default=str(DEFAULT_KEY_PATH))
    parser.add_argument("--log-raw", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_task_settings(args.config, args.task)
    hmac_key = load_hmac_key(key_file=args.hmac_key_file)
    detector = ScreenCommandDetector(tuple(action.token for action in settings.actions))
    runtime = TaskRuntime(settings, hmac_key)

    import serial

    screen = serial.Serial()
    screen.port = settings.serial.screen_port
    screen.baudrate = settings.serial.screen_baudrate
    screen.bytesize = serial.EIGHTBITS
    screen.parity = serial.PARITY_NONE
    screen.stopbits = serial.STOPBITS_ONE
    screen.timeout = 0.05

    runtime.start()
    try:
        screen.open()
        print(
            f"Task {settings.name!r}: screen {settings.serial.screen_port} @ "
            f"{settings.serial.screen_baudrate}; HC-14 {settings.serial.hc14_port} @ "
            f"{settings.serial.hc14_baudrate}",
            flush=True,
        )
        while True:
            data = screen.read(screen.in_waiting or 1)
            if data and args.log_raw:
                print(f"SCREEN RX {data.hex(' ')} {data!r}", flush=True)
            for token in detector.feed(data):
                runtime.handle_screen_token(token)
            runtime.tick()
    except KeyboardInterrupt:
        print("Stopping task template", flush=True)
    finally:
        if screen.is_open:
            screen.close()
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
