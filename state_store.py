from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import time

from models import FCState, MissionState, MissionStatus, RejectReason


@dataclass
class LinkState:
    connected: bool = False
    last_rx_time: float | None = None
    last_packet_time: float | None = None
    session: int | None = None
    telemetry_hz: float = 0.0
    alarm: str = ""


@dataclass
class MissionSnapshot:
    state: MissionState = MissionState.IDLE
    target1: int | None = None
    target2: int | None = None
    progress: int = 0
    message: str = ""
    error_code: int = 0
    error: str = ""
    updated_at: float | None = None


@dataclass
class StateStore:
    stale_after_seconds: float = 1.5
    telemetry: FCState | None = None
    link: LinkState = field(default_factory=LinkState)
    mission: MissionSnapshot = field(default_factory=MissionSnapshot)
    last_command: str = ""
    last_ack: str = ""
    _telemetry_times: deque[float] = field(
        default_factory=lambda: deque(maxlen=30), init=False, repr=False
    )

    def update_telemetry(
        self, telemetry: FCState, *, session: int, now: float | None = None
    ) -> None:
        timestamp = time.monotonic() if now is None else now
        self.telemetry = telemetry
        self.link.connected = True
        self.link.last_rx_time = timestamp
        self.link.last_packet_time = timestamp
        self.link.session = session
        self.link.alarm = ""
        if self._telemetry_times and timestamp <= self._telemetry_times[-1]:
            self._telemetry_times.clear()
        self._telemetry_times.append(timestamp)
        if len(self._telemetry_times) >= 2:
            elapsed = self._telemetry_times[-1] - self._telemetry_times[0]
            self.link.telemetry_hz = (
                (len(self._telemetry_times) - 1) / elapsed if elapsed > 0 else 0.0
            )

    def note_link_activity(
        self, *, session: int, now: float | None = None
    ) -> None:
        timestamp = time.monotonic() if now is None else now
        self.link.connected = True
        self.link.last_packet_time = timestamp
        self.link.session = session

    def update_mission(
        self, status: MissionStatus, *, now: float | None = None
    ) -> None:
        self.mission.state = status.state
        self.mission.target1 = status.target1
        self.mission.target2 = status.target2
        self.mission.progress = status.progress
        self.mission.message = status.message
        self.mission.error_code = status.error_code
        self.mission.error = status.message if status.error_code else ""
        self.mission.updated_at = time.monotonic() if now is None else now

    def mark_disconnected(self, alarm: str = "link disconnected") -> None:
        self.link.connected = False
        self.link.telemetry_hz = 0.0
        self.link.alarm = alarm

    def telemetry_age(self, *, now: float | None = None) -> float | None:
        if self.link.last_rx_time is None:
            return None
        timestamp = time.monotonic() if now is None else now
        return max(0.0, timestamp - self.link.last_rx_time)

    def is_stale(self, *, now: float | None = None) -> bool:
        age = self.telemetry_age(now=now)
        return age is None or age > self.stale_after_seconds

    def mission_age(self, *, now: float | None = None) -> float | None:
        if self.mission.updated_at is None:
            return None
        timestamp = time.monotonic() if now is None else now
        return max(0.0, timestamp - self.mission.updated_at)

    def reject_reason_for_start(self, *, now: float | None = None) -> RejectReason:
        reason = self.reject_reason_for_new_task(now=now)
        if reason != RejectReason.NONE:
            return reason
        if self.mission.target1 is None or self.mission.target2 is None:
            return RejectReason.TARGETS_NOT_READY
        return RejectReason.NONE

    def reject_reason_for_new_task(self, *, now: float | None = None) -> RejectReason:
        if not self.link.connected:
            return RejectReason.LINK_DOWN
        if self.is_stale(now=now):
            return RejectReason.STALE_TELEMETRY
        if self.mission.state not in (
            MissionState.IDLE,
            MissionState.READY,
            MissionState.COMPLETED,
            MissionState.FAILED,
        ):
            return RejectReason.TASK_BUSY
        return RejectReason.NONE
