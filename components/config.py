from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_PORT = "/dev/serial/by-id/<hc14-usb-serial>"
DEFAULT_BAUDRATE = 9600
DEFAULT_TELEMETRY_STALE_SECONDS = 1.5
DEFAULT_COMMAND_TIMEOUT_SECONDS = 0.8
DEFAULT_COMMAND_RETRIES = 3


@dataclass(frozen=True)
class Settings:
    serial_port: str
    baudrate: int
    hmac_key: bytes
    telemetry_stale_seconds: float = DEFAULT_TELEMETRY_STALE_SECONDS
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS
    command_retries: int = DEFAULT_COMMAND_RETRIES


def load_hmac_key(
    env_name: str = "GROUND_STATION_HMAC_KEY_HEX",
    key_file: str | Path = "config/secrets/hmac.key",
) -> bytes:
    env_value = os.getenv(env_name)
    if env_value:
        return bytes.fromhex(env_value.strip())

    path = Path(key_file)
    if path.exists():
        raw = path.read_text(encoding="ascii").strip()
        return bytes.fromhex(raw)

    raise RuntimeError(
        "Missing HMAC key. Set GROUND_STATION_HMAC_KEY_HEX or create "
        "config/secrets/hmac.key with hex-encoded random bytes."
    )


def load_settings() -> Settings:
    port = os.getenv("GROUND_STATION_SERIAL_PORT", DEFAULT_PORT)
    baudrate = int(os.getenv("GROUND_STATION_BAUDRATE", str(DEFAULT_BAUDRATE)))
    return Settings(
        serial_port=port,
        baudrate=baudrate,
        hmac_key=load_hmac_key(),
    )

