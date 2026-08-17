from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Any, Callable, Mapping, Optional

from .buzzer_control import build_ground_buzzer, trigger_buzzer
from .led_control import build_ground_led, GroundLedClient


LOG = logging.getLogger("ground-cue-player")
# Default brightness for competition cues; kept as a module-level fallback.
# Entry programs pass GroundCueSettings (possibly from the D-task config's
# ``ground_cues`` section) to override brightness and colors.
CUE_BRIGHTNESS = 20


@dataclass(frozen=True)
class GroundCueSettings:
    """Presentation parameters for ground light/buzzer cues.

    These are competition-task presentation parameters, not hardware
    settings: the player never touches GPIO directly, it only talks to the
    LED client and the buzzer callback.  Durations/counts shared with the
    mission cue timing configs stay there (``mission1_cues`` /
    ``mission2_cues``) to avoid storing the same parameter twice.
    """

    brightness: int = CUE_BRIGHTNESS
    start_notice_color: tuple[int, int, int] = (255, 0, 0)
    start_notice_count: int = 3
    escort_color: tuple[int, int, int] = (255, 255, 255)
    escort_count: int = 3
    drop_color: tuple[int, int, int] = (255, 0, 0)
    completion_color: tuple[int, int, int] = (0, 255, 0)
    target_locked_color: tuple[int, int, int] = (0, 255, 0)
    retakeoff_color: tuple[int, int, int] = (0, 255, 0)

    @classmethod
    def from_config(cls, value: Optional[Mapping[str, Any]]) -> "GroundCueSettings":
        """Parse the D-task config ``ground_cues`` section (colors/brightness only)."""
        if not value:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("ground_cues must be a JSON object")

        def _color(cue_name: str, section: Mapping[str, Any], default) -> tuple[int, int, int]:
            if "color" not in section:
                return default
            raw = section["color"]
            path = f"ground_cues.{cue_name}.color"
            if not isinstance(raw, list) or len(raw) != 3:
                raise ValueError(f"{path} must contain three RGB values")
            channels: list[int] = []
            for index, channel in enumerate(raw):
                if isinstance(channel, bool) or not isinstance(channel, int):
                    raise ValueError(
                        f"{path}[{index}] must be an integer between 0 and 255"
                    )
                if not 0 <= channel <= 255:
                    raise ValueError(
                        f"{path}[{index}] must be an integer between 0 and 255"
                    )
                channels.append(channel)
            return tuple(channels)

        def _count(section: Mapping[str, Any], key: str, default: int) -> int:
            if key not in section:
                return default
            raw = section[key]
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
                raise ValueError(f"ground_cues.{key}.count must be a positive integer")
            return raw

        start_notice = value.get("start_notice") or {}
        escort = value.get("escort_acquired") or {}
        mission1_drop = value.get("mission1_drop") or {}
        mission_completed = value.get("mission_completed") or {}
        mission2_target = value.get("mission2_target_locked") or {}
        mission2_retakeoff = value.get("mission2_retakeoff") or {}
        for name, section in (
            ("start_notice", start_notice),
            ("escort_acquired", escort),
            ("mission1_drop", mission1_drop),
            ("mission_completed", mission_completed),
            ("mission2_target_locked", mission2_target),
            ("mission2_retakeoff", mission2_retakeoff),
        ):
            if not isinstance(section, dict):
                raise ValueError(f"ground_cues.{name} must be a JSON object")

        brightness = value.get("brightness", CUE_BRIGHTNESS)
        if isinstance(brightness, bool) or not isinstance(brightness, int):
            raise ValueError("ground_cues.brightness must be an integer")
        if not 0 <= brightness <= 255:
            raise ValueError("ground_cues.brightness must be between 0 and 255")

        return cls(
            brightness=brightness,
            start_notice_color=_color("start_notice", start_notice, (255, 0, 0)),
            start_notice_count=_count(start_notice, "count", 3),
            escort_color=_color("escort_acquired", escort, (255, 255, 255)),
            escort_count=_count(escort, "count", 3),
            drop_color=_color("mission1_drop", mission1_drop, (255, 0, 0)),
            completion_color=_color("mission_completed", mission_completed, (0, 255, 0)),
            target_locked_color=_color("mission2_target_locked", mission2_target, (0, 255, 0)),
            retakeoff_color=_color("mission2_retakeoff", mission2_retakeoff, (0, 255, 0)),
        )


class GroundCuePlayer:
    """Serialize best-effort ground-station light and buzzer cues."""

    def __init__(
        self,
        *,
        led: Optional[GroundLedClient] = None,
        buzzer: Callable[[float], None] = trigger_buzzer,
        wait: Callable[[float], None] = time.sleep,
        settings: Optional[GroundCueSettings] = None,
    ) -> None:
        self._led = GroundLedClient() if led is None else led
        self._buzzer = buzzer
        self._wait = wait
        self._settings = settings if settings is not None else GroundCueSettings()
        self._lock = threading.Lock()

    def _pulse(
        self,
        *,
        color: tuple[int, int, int],
        brightness: int,
        on_seconds: float,
    ) -> None:
        led_enabled = False
        try:
            try:
                self._led.solid(color, brightness=brightness)
                led_enabled = True
            except Exception:
                LOG.exception("failed to enable ground LED cue")

            try:
                self._buzzer(on_seconds)
            except Exception:
                LOG.exception("failed to play ground buzzer cue")
                if led_enabled:
                    self._wait(on_seconds)
        finally:
            try:
                self._led.off()
            except Exception:
                LOG.exception("failed to turn off ground LED cue")

    def play_start_notice(
        self,
        *,
        on_seconds: float,
        off_seconds: float,
    ) -> None:
        with self._lock:
            for index in range(self._settings.start_notice_count):
                self._pulse(
                    color=self._settings.start_notice_color,
                    brightness=self._settings.brightness,
                    on_seconds=on_seconds,
                )
                if index < self._settings.start_notice_count - 1:
                    self._wait(off_seconds)

    def play_mission1_escort_acquired(
        self,
        *,
        on_seconds: float = 0.2,
        off_seconds: float = 0.2,
    ) -> None:
        with self._lock:
            self._play_escort_acquired(on_seconds, off_seconds)

    def play_mission2_escort_acquired(
        self,
        *,
        on_seconds: float = 0.2,
        off_seconds: float = 0.2,
    ) -> None:
        with self._lock:
            self._play_escort_acquired(on_seconds, off_seconds)

    def _play_escort_acquired(
        self,
        on_seconds: float,
        off_seconds: float,
    ) -> None:
        for index in range(self._settings.escort_count):
            self._pulse(
                color=self._settings.escort_color,
                brightness=self._settings.brightness,
                on_seconds=on_seconds,
            )
            if index < self._settings.escort_count - 1:
                self._wait(off_seconds)

    def play_mission1_drop(
        self,
        *,
        duration_seconds: float = 1.0,
    ) -> None:
        with self._lock:
            self._pulse(
                color=self._settings.drop_color,
                brightness=self._settings.brightness,
                on_seconds=duration_seconds,
            )

    def play_mission1_completed(
        self,
        *,
        duration_seconds: float = 1.0,
    ) -> None:
        with self._lock:
            self._play_green_completion(duration_seconds)

    def play_mission2_target_locked(
        self,
        *,
        duration_seconds: float = 1.0,
    ) -> None:
        with self._lock:
            self._pulse(
                color=self._settings.target_locked_color,
                brightness=self._settings.brightness,
                on_seconds=duration_seconds,
            )

    def play_mission2_retakeoff_started(
        self,
        *,
        duration_seconds: float = 1.0,
    ) -> None:
        with self._lock:
            self._pulse(
                color=self._settings.retakeoff_color,
                brightness=self._settings.brightness,
                on_seconds=duration_seconds,
            )

    def play_mission2_completed(
        self,
        *,
        duration_seconds: float = 1.0,
    ) -> None:
        with self._lock:
            self._play_green_completion(duration_seconds)

    def _play_green_completion(self, duration_seconds: float) -> None:
        try:
            try:
                self._led.solid(
                    self._settings.completion_color,
                    brightness=self._settings.brightness,
                )
            except Exception:
                LOG.exception("failed to enable mission-complete LED")

            try:
                self._buzzer(duration_seconds)
            except Exception:
                LOG.exception("failed to play mission-complete buzzer")
                self._wait(duration_seconds)
        finally:
            try:
                self._led.flow()
            except Exception:
                LOG.exception("failed to restore default LED flow")

    def turn_off(self) -> None:
        with self._lock:
            try:
                self._led.off()
            except Exception:
                LOG.exception("failed to turn off ground LED during shutdown")


def build_ground_cue_player(station, settings: Optional[GroundCueSettings] = None) -> GroundCuePlayer:
    """Build a cue player wired to the station's LED client and buzzer callback."""
    return GroundCuePlayer(
        led=build_ground_led(station),
        buzzer=build_ground_buzzer(station),
        settings=settings,
    )
