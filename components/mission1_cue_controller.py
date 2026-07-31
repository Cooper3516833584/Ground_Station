from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
import logging
import queue
import threading
from typing import Callable, Mapping, Optional

from .ground_cue_player import GroundCuePlayer


LOG = logging.getLogger("mission1-cue-controller")

CAR_FOLLOWING = 4
CAR_ARRIVED = 7
CAR_FAILED = 11
CAR_CLOSED = 12
CAR_MISSION1_REQUESTED = 13

DRONE_DISPATCHER_READY = 30
DRONE_ESCORTING = 5
DRONE_MISSION1_DROP_COMPLETED = 14


class CueKind(Enum):
    ESCORT_ACQUIRED = auto()
    DROP = auto()
    COMPLETED = auto()


@dataclass(frozen=True)
class Mission1CueTiming:
    monitor_interval_s: float = 0.1
    escort_on_s: float = 0.2
    escort_off_s: float = 0.2
    drop_duration_s: float = 1.0
    completion_duration_s: float = 1.0

    @classmethod
    def from_config(cls, value: Optional[Mapping[str, object]]):
        if not value:
            return cls()
        return cls(
            monitor_interval_s=float(
                value.get("monitor_interval_seconds", 0.1)
            ),
            escort_on_s=float(value.get("escort_on_seconds", 0.2)),
            escort_off_s=float(value.get("escort_off_seconds", 0.2)),
            drop_duration_s=float(
                value.get("drop_duration_seconds", 1.0)
            ),
            completion_duration_s=float(
                value.get("completion_duration_seconds", 1.0)
            ),
        )

    def __post_init__(self) -> None:
        if min(
            self.monitor_interval_s,
            self.escort_on_s,
            self.escort_off_s,
            self.drop_duration_s,
            self.completion_duration_s,
        ) <= 0:
            raise ValueError("Mission 1 cue timing values must be positive")


@dataclass
class Mission1CueRun:
    car_session: int
    initial_drone_session: Optional[int]
    drone_task_session: Optional[int] = None
    seen_car_following: bool = False
    escort_cue_fired: bool = False
    drop_cue_fired: bool = False
    completion_cue_fired: bool = False


class Mission1CueController:
    """Detect MISSION1 state edges and play cues outside polling threads."""

    def __init__(
        self,
        snapshot_provider: Callable,
        *,
        cue_player: Optional[GroundCuePlayer] = None,
        timing: Mission1CueTiming = Mission1CueTiming(),
        queue_capacity: int = 8,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        self._snapshot_provider = snapshot_provider
        self._cue_player = (
            GroundCuePlayer() if cue_player is None else cue_player
        )
        self._timing = timing
        self._queue = queue.Queue(maxsize=queue_capacity)
        self._stop = threading.Event()
        self._monitor_thread = None
        self._worker_thread = None
        self._run = None
        self._last_car_state = None
        self._last_car_session = None
        self._queued_keys = set()

    def start(self) -> None:
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._stop.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="mission1-cue-worker",
            daemon=True,
        )
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="mission1-cue-monitor",
            daemon=True,
        )
        self._worker_thread.start()
        self._monitor_thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=1.0)
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=2.0)
        self._run = None

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._observe(self._snapshot_provider())
            except Exception:
                LOG.exception("MISSION1 cue monitor failed")
            self._stop.wait(self._timing.monitor_interval_s)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None or self._stop.is_set():
                return
            _key, cue_kind = item
            try:
                self._play(cue_kind)
            except Exception:
                LOG.exception("MISSION1 cue %s failed", cue_kind.name)

    def _play(self, cue_kind: CueKind) -> None:
        if cue_kind is CueKind.ESCORT_ACQUIRED:
            self._cue_player.play_mission1_escort_acquired(
                on_seconds=self._timing.escort_on_s,
                off_seconds=self._timing.escort_off_s,
            )
        elif cue_kind is CueKind.DROP:
            self._cue_player.play_mission1_drop(
                duration_seconds=self._timing.drop_duration_s,
            )
        elif cue_kind is CueKind.COMPLETED:
            self._cue_player.play_mission1_completed(
                duration_seconds=self._timing.completion_duration_s,
            )

    def _observe(self, snapshot) -> None:
        car = snapshot.car
        drone = snapshot.drone
        car_state = car.operation_state if car.online else None
        entered_mission1 = (
            car.online
            and car.session is not None
            and car_state == CAR_MISSION1_REQUESTED
            and (
                self._last_car_state != CAR_MISSION1_REQUESTED
                or self._last_car_session != car.session
            )
        )
        if entered_mission1:
            self._queued_keys.clear()
            self._run = Mission1CueRun(
                car_session=car.session,
                initial_drone_session=drone.session,
            )

        run = self._run
        if run is not None:
            if car.session != run.car_session or car_state in (
                CAR_FAILED,
                CAR_CLOSED,
            ):
                self._run = None
            else:
                self._observe_active_run(run, car, drone)

        self._last_car_state = car_state
        self._last_car_session = car.session

    def _observe_active_run(self, run, car, drone) -> None:
        if (
            run.drone_task_session is None
            and drone.online
            and drone.session is not None
            and drone.session != run.initial_drone_session
            and drone.operation_state != DRONE_DISPATCHER_READY
        ):
            run.drone_task_session = drone.session

        valid_drone_task = (
            run.drone_task_session is not None
            and drone.online
            and drone.session == run.drone_task_session
        )
        if (
            valid_drone_task
            and drone.operation_state == DRONE_ESCORTING
            and not run.escort_cue_fired
        ):
            run.escort_cue_fired = True
            self._submit(run, CueKind.ESCORT_ACQUIRED)

        if (
            valid_drone_task
            and drone.operation_state == DRONE_MISSION1_DROP_COMPLETED
            and not run.drop_cue_fired
        ):
            run.drop_cue_fired = True
            self._submit(run, CueKind.DROP)

        if (
            car.online
            and car.session == run.car_session
            and car.operation_state == CAR_FOLLOWING
        ):
            run.seen_car_following = True
        if (
            run.seen_car_following
            and car.online
            and car.session == run.car_session
            and car.operation_state == CAR_ARRIVED
            and not run.completion_cue_fired
        ):
            run.completion_cue_fired = True
            self._submit(run, CueKind.COMPLETED)
            self._run = None

    def _submit(self, run: Mission1CueRun, cue_kind: CueKind) -> None:
        if self._stop.is_set():
            return
        key = (run.car_session, run.drone_task_session, cue_kind)
        if key in self._queued_keys:
            return
        self._queued_keys.add(key)
        try:
            self._queue.put_nowait((key, cue_kind))
        except queue.Full:
            LOG.warning("MISSION1 cue queue is full; dropping %s", cue_kind.name)
