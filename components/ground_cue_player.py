from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .buzzer_control import trigger_buzzer
from .led_control import GroundLedClient


LOG = logging.getLogger("ground-cue-player")
CUE_BRIGHTNESS = 20


class GroundCuePlayer:
    """Serialize best-effort ground-station light and buzzer cues."""

    def __init__(
        self,
        *,
        led: Optional[GroundLedClient] = None,
        buzzer: Callable[[float], None] = trigger_buzzer,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        self._led = GroundLedClient() if led is None else led
        self._buzzer = buzzer
        self._wait = wait
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
            for index in range(3):
                self._pulse(
                    color=(255, 0, 0),
                    brightness=CUE_BRIGHTNESS,
                    on_seconds=on_seconds,
                )
                if index < 2:
                    self._wait(off_seconds)

    def play_mission1_escort_acquired(
        self,
        *,
        on_seconds: float = 0.2,
        off_seconds: float = 0.2,
    ) -> None:
        with self._lock:
            for index in range(3):
                self._pulse(
                    color=(255, 255, 255),
                    brightness=CUE_BRIGHTNESS,
                    on_seconds=on_seconds,
                )
                if index < 2:
                    self._wait(off_seconds)

    def play_mission1_drop(
        self,
        *,
        duration_seconds: float = 1.0,
    ) -> None:
        with self._lock:
            self._pulse(
                color=(255, 0, 0),
                brightness=CUE_BRIGHTNESS,
                on_seconds=duration_seconds,
            )

    def play_mission1_completed(
        self,
        *,
        duration_seconds: float = 1.0,
    ) -> None:
        with self._lock:
            try:
                try:
                    self._led.solid(
                        (0, 255, 0),
                        brightness=CUE_BRIGHTNESS,
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
