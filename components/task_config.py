from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .models import Command, CommandId


@dataclass(frozen=True)
class SerialSettings:
    screen_port: str
    screen_baudrate: int
    hc14_port: str
    hc14_baudrate: int
    cooldown_seconds: float


@dataclass(frozen=True)
class LedSettings:
    mode: str
    color: tuple[int, int, int] = (0, 0, 0)
    brightness: int = 3
    interval_seconds: float = 0.5


@dataclass(frozen=True)
class ScreenAction:
    token: str
    command: Command
    led: LedSettings | None = None


@dataclass(frozen=True)
class TaskSettings:
    name: str
    serial: SerialSettings
    startup_led: LedSettings
    actions: tuple[ScreenAction, ...]


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a JSON object")
    return value


def _require_int(value: Any, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{path} must be between {minimum} and {maximum}")
    return value


def _parse_color(value: Any, path: str) -> tuple[int, int, int]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{path} must contain exactly three RGB values")
    return tuple(
        _require_int(channel, f"{path}[{index}]", 0, 255)
        for index, channel in enumerate(value)
    )


def _parse_led(value: Any, path: str) -> LedSettings:
    data = _require_object(value, path)
    mode = data.get("mode", "off")
    if mode not in {"off", "solid", "blink", "flow"}:
        raise ValueError(f"{path}.mode must be off, solid, blink, or flow")
    color = _parse_color(data.get("color", [0, 0, 0]), f"{path}.color")
    brightness = _require_int(data.get("brightness", 3), f"{path}.brightness", 0, 20)
    interval = data.get("interval_seconds", 0.5)
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        raise ValueError(f"{path}.interval_seconds must be a number")
    interval = float(interval)
    if not 0.05 <= interval <= 60.0:
        raise ValueError(f"{path}.interval_seconds must be between 0.05 and 60")
    return LedSettings(mode, color, brightness, interval)


def _parse_command(value: Any, path: str) -> Command:
    data = _require_object(value, path)
    name = data.get("name")
    if not isinstance(name, str):
        raise ValueError(f"{path}.name must be a command name")
    try:
        command_id = CommandId[name]
    except KeyError as exc:
        allowed = ", ".join(command.name for command in CommandId)
        raise ValueError(f"{path}.name must be one of: {allowed}") from exc

    if command_id == CommandId.SET_TARGETS:
        target1 = _require_int(data.get("target1"), f"{path}.target1", 0, 255)
        target2 = _require_int(data.get("target2"), f"{path}.target2", 0, 255)
        return Command(command_id, target1, target2)
    if "target1" in data or "target2" in data:
        raise ValueError(f"{path} targets are only valid for SET_TARGETS")
    return Command(command_id)


def load_task_settings(path: str | Path, task_name: str | None = None) -> TaskSettings:
    config_path = Path(path)
    try:
        root = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {config_path}: {exc}") from exc
    root = _require_object(root, "config")

    selected_name = task_name or root.get("active_task")
    if not isinstance(selected_name, str) or not selected_name:
        raise ValueError("active_task must select a task")
    tasks = _require_object(root.get("tasks"), "tasks")
    if selected_name not in tasks:
        raise ValueError(f"task {selected_name!r} is not defined")
    task = _require_object(tasks[selected_name], f"tasks.{selected_name}")

    serial_data = _require_object(root.get("serial"), "serial")
    screen_port = serial_data.get("screen_port")
    hc14_port = serial_data.get("hc14_port")
    if not isinstance(screen_port, str) or not screen_port:
        raise ValueError("serial.screen_port must be a non-empty string")
    if not isinstance(hc14_port, str) or not hc14_port:
        raise ValueError("serial.hc14_port must be a non-empty string")
    cooldown = serial_data.get("cooldown_seconds", 0.75)
    if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)):
        raise ValueError("serial.cooldown_seconds must be a number")
    if not 0.0 <= float(cooldown) <= 60.0:
        raise ValueError("serial.cooldown_seconds must be between 0 and 60")
    serial = SerialSettings(
        screen_port=screen_port,
        screen_baudrate=_require_int(
            serial_data.get("screen_baudrate"), "serial.screen_baudrate", 1, 4_000_000
        ),
        hc14_port=hc14_port,
        hc14_baudrate=_require_int(
            serial_data.get("hc14_baudrate"), "serial.hc14_baudrate", 1, 4_000_000
        ),
        cooldown_seconds=float(cooldown),
    )

    actions_data = _require_object(task.get("screen_commands"), f"tasks.{selected_name}.screen_commands")
    actions = []
    seen_tokens = set()
    for token, value in actions_data.items():
        if not isinstance(token, str) or not token or len(token.encode("utf-8")) > 64:
            raise ValueError("screen command tokens must contain 1 to 64 UTF-8 bytes")
        normalized = token.upper()
        if normalized in seen_tokens:
            raise ValueError(f"duplicate case-insensitive screen token: {token!r}")
        seen_tokens.add(normalized)
        action_data = _require_object(value, f"screen_commands.{token}")
        led = (
            _parse_led(action_data["led"], f"screen_commands.{token}.led")
            if "led" in action_data
            else None
        )
        actions.append(
            ScreenAction(
                token=token,
                command=_parse_command(
                    action_data.get("aircraft_command"),
                    f"screen_commands.{token}.aircraft_command",
                ),
                led=led,
            )
        )
    if not actions:
        raise ValueError(f"task {selected_name!r} must define at least one screen command")

    return TaskSettings(
        name=selected_name,
        serial=serial,
        startup_led=_parse_led(task.get("startup_led", {"mode": "off"}), "startup_led"),
        actions=tuple(actions),
    )
