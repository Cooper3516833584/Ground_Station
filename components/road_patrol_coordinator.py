"""Ground-side coordination for the road-patrol aircraft and car."""

from dataclasses import dataclass
import logging
import queue
import threading
import time

from .fleet_models import (
    AckStatus,
    CommandId,
    CommandPayload,
    MessageKind,
    NodeFlags,
    NodeId,
)
from .fleet_protocol import decode_ack
from .ground_cue_player import GroundCuePlayer
from .road_patrol_status import (
    RoadPatrolOperationState,
    RoadPatrolStage,
    stage_for_operation_state,
)


LOG = logging.getLogger("road-patrol-coordinator")


@dataclass(frozen=True)
class RoadPatrolTiming:
    monitor_interval_s: float = 0.1
    cue_on_s: float = 0.2
    cue_off_s: float = 0.2
    car_ready_timeout_s: float = 60.0
    command_timeout_s: float = 10.0
    command_attempts: int = 3
    command_retry_interval_s: float = 0.5
    takeoff_ready_wait_s: float = 15.0
    takeoff_warning_s: float = 5.0
    takeoff_prepare_timeout_s: float = 10.0

    @classmethod
    def from_config(cls, value):
        if not value:
            return cls()
        return cls(
            monitor_interval_s=float(value.get("monitor_interval_seconds", 0.1)),
            cue_on_s=float(value.get("cue_on_seconds", 0.2)),
            cue_off_s=float(value.get("cue_off_seconds", 0.2)),
            car_ready_timeout_s=float(value.get("car_ready_timeout_seconds", 60.0)),
            command_timeout_s=float(value.get("command_timeout_seconds", 10.0)),
            command_attempts=int(value.get("command_attempts", 3)),
            command_retry_interval_s=float(
                value.get("command_retry_interval_seconds", 0.5)
            ),
            takeoff_ready_wait_s=float(
                value.get("takeoff_ready_wait_seconds", 15.0)
            ),
            takeoff_warning_s=float(
                value.get("takeoff_warning_seconds", 5.0)
            ),
            takeoff_prepare_timeout_s=float(
                value.get("takeoff_prepare_timeout_seconds", 10.0)
            ),
        )

    def __post_init__(self):
        values = (
            self.monitor_interval_s,
            self.cue_on_s,
            self.cue_off_s,
            self.car_ready_timeout_s,
            self.command_timeout_s,
            self.command_retry_interval_s,
            self.takeoff_ready_wait_s,
            self.takeoff_warning_s,
            self.takeoff_prepare_timeout_s,
        )
        if min(values) <= 0 or self.command_attempts <= 0:
            raise ValueError("road-patrol timing values must be positive")


class RoadPatrolCoordinator:
    """Observe aircraft stages and dispatch cues/car start outside I/O threads."""

    _SENTINEL = object()

    def __init__(
        self,
        master,
        snapshot_provider,
        *,
        timing=RoadPatrolTiming(),
        cue_player=None,
        wait=None,
        monotonic=time.monotonic,
    ):
        self._master = master
        self._snapshot_provider = snapshot_provider
        self._timing = timing
        self._cue_player = GroundCuePlayer() if cue_player is None else cue_player
        self._wait = wait
        self._monotonic = monotonic
        self._stop = threading.Event()
        self._cue_queue = queue.Queue()
        self._car_start_queue = queue.Queue()
        self._takeoff_queue = queue.Queue()
        self._lock = threading.Lock()
        self._drone_session = None
        self._last_operation_state = None
        self._last_stage = None
        self._release_seen_sessions = set()
        self._takeoff_seen_sessions = set()
        self._threads = []

    def start(self):
        if any(thread.is_alive() for thread in self._threads):
            return
        self._stop.clear()
        self._threads = [
            threading.Thread(
                target=self._monitor_loop,
                name="road-patrol-monitor",
                daemon=True,
            ),
            threading.Thread(
                target=self._cue_loop,
                name="road-patrol-cues",
                daemon=True,
            ),
            threading.Thread(
                target=self._car_start_loop,
                name="road-patrol-car-start",
                daemon=True,
            ),
            threading.Thread(
                target=self._takeoff_loop,
                name="road-patrol-takeoff",
                daemon=True,
            ),
        ]
        for thread in self._threads:
            thread.start()

    def close(self):
        self._stop.set()
        self._cue_queue.put(self._SENTINEL)
        self._car_start_queue.put(self._SENTINEL)
        self._takeoff_queue.put(self._SENTINEL)
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads = []
        self._cue_player.turn_off()

    def observe_snapshot(self, snapshot):
        """Process one snapshot; exposed to keep transition behavior testable."""

        drone = snapshot.drone
        if not drone.online or drone.stale or drone.session is None:
            return
        operation_state = int(drone.operation_state)
        stage = stage_for_operation_state(operation_state)
        with self._lock:
            if drone.session != self._drone_session:
                self._drone_session = drone.session
                self._last_operation_state = operation_state
                self._last_stage = stage
                stage_changed = (
                    stage is not None and stage is not RoadPatrolStage.STANDBY
                )
                release_entered = False
                if operation_state == int(
                    RoadPatrolOperationState.PAYLOAD_RELEASED
                ):
                    self._release_seen_sessions.add(drone.session)
            else:
                previous_operation_state = self._last_operation_state
                previous_stage = self._last_stage
                if operation_state == previous_operation_state:
                    return
                self._last_operation_state = operation_state
                if stage is not None:
                    self._last_stage = stage

                stage_changed = stage is not None and stage != previous_stage
                release_entered = (
                    operation_state
                    == int(RoadPatrolOperationState.PAYLOAD_RELEASED)
                    and previous_operation_state
                    != int(RoadPatrolOperationState.PAYLOAD_RELEASED)
                    and drone.session not in self._release_seen_sessions
                )
                if release_entered:
                    self._release_seen_sessions.add(drone.session)

            takeoff_entered = (
                operation_state == int(RoadPatrolOperationState.TAKEOFF)
                and drone.session not in self._takeoff_seen_sessions
            )
            if takeoff_entered:
                self._takeoff_seen_sessions.add(drone.session)

        if stage_changed and not takeoff_entered:
            self._cue_queue.put(stage)
        if release_entered:
            self._car_start_queue.put(drone.session)
        if takeoff_entered:
            self._takeoff_queue.put(drone.session)

    def _monitor_loop(self):
        while not self._stop.is_set():
            try:
                self.observe_snapshot(self._snapshot_provider())
            except Exception:
                LOG.exception("road-patrol snapshot processing failed")
            if self._stop.wait(self._timing.monitor_interval_s):
                return

    def _cue_loop(self):
        while not self._stop.is_set():
            try:
                stage = self._cue_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if stage is self._SENTINEL:
                return
            try:
                LOG.info("road-patrol stage changed to %s", stage.value)
                self._cue_player.play_stage_transition(
                    on_seconds=self._timing.cue_on_s,
                    off_seconds=self._timing.cue_off_s,
                )
            except Exception:
                LOG.exception("road-patrol stage cue failed")

    def _takeoff_loop(self):
        while not self._stop.is_set():
            try:
                drone_session = self._takeoff_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if drone_session is self._SENTINEL:
                return
            try:
                self._run_takeoff_sequence(drone_session)
            except Exception:
                LOG.exception(
                    "ground-controlled takeoff sequence failed for drone session %s",
                    drone_session,
                )

    def _run_takeoff_sequence(self, drone_session):
        if not self._drone_waiting_for_takeoff(drone_session):
            return False
        self._cue_player.play_stage_transition(
            on_seconds=self._timing.cue_on_s,
            off_seconds=self._timing.cue_off_s,
        )
        if not self._drone_waiting_for_takeoff(drone_session):
            return False

        prepare_seq = self._send_command(
            NodeId.DRONE,
            CommandId.DRONE_PREPARE_MISSION,
        )
        deadline = self._monotonic() + self._timing.takeoff_prepare_timeout_s
        while not self._stop.is_set():
            snapshot = self._snapshot_provider()
            drone = snapshot.drone
            if not self._drone_waiting_for_takeoff(drone_session, snapshot):
                return False
            if (
                drone.active_command_seq == prepare_seq
                and drone.active_command_status == int(AckStatus.COMPLETED)
            ):
                break
            if self._monotonic() >= deadline:
                raise TimeoutError("timed out waiting for drone prepare completion")
            self._sleep(0.1)
        if self._stop.is_set():
            return False

        if not self._wait_while_takeoff_pending(
            drone_session,
            self._timing.takeoff_ready_wait_s,
        ):
            return False
        self._cue_player.play_takeoff_warning(
            duration_seconds=self._timing.takeoff_warning_s,
        )
        if (
            self._stop.is_set()
            or not self._drone_waiting_for_takeoff(drone_session)
        ):
            return False
        self._send_command(NodeId.DRONE, CommandId.DRONE_START_MISSION)
        LOG.info("ground countdown complete; drone takeoff command acknowledged")
        return True

    def _wait_while_takeoff_pending(self, drone_session, duration):
        deadline = self._monotonic() + duration
        while not self._stop.is_set():
            if not self._drone_waiting_for_takeoff(drone_session):
                return False
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return True
            self._sleep(min(0.1, remaining))
        return False

    def _drone_waiting_for_takeoff(self, drone_session, snapshot=None):
        current = self._snapshot_provider() if snapshot is None else snapshot
        drone = current.drone
        return bool(
            drone.online
            and not drone.stale
            and drone.session == drone_session
            and drone.operation_state == int(RoadPatrolOperationState.TAKEOFF)
        )

    def _car_start_loop(self):
        while not self._stop.is_set():
            try:
                drone_session = self._car_start_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if drone_session is self._SENTINEL:
                return
            try:
                self._dispatch_car_start(drone_session)
            except Exception:
                LOG.exception(
                    "car start failed after payload release in drone session %s",
                    drone_session,
                )

    def _dispatch_car_start(self, drone_session):
        deadline = self._monotonic() + self._timing.car_ready_timeout_s
        while not self._stop.is_set():
            with self._lock:
                if self._drone_session != drone_session:
                    LOG.info(
                        "discarded car start from superseded drone session %s",
                        drone_session,
                    )
                    return False
            snapshot = self._snapshot_provider()
            car = snapshot.car
            if (
                car.online
                and not car.stale
                and car.node_flags & int(NodeFlags.READY)
            ):
                break
            if self._monotonic() >= deadline:
                raise TimeoutError("timed out waiting for road-patrol car readiness")
            self._sleep(min(0.1, max(0.0, deadline - self._monotonic())))
        if self._stop.is_set():
            return False

        self._send_command(NodeId.CAR, CommandId.CAR_START_MISSION)
        LOG.info("car mission started after confirmed aircraft payload release")
        return True

    def _send_command(self, node_id, command_id):
        error = None
        for attempt in range(1, self._timing.command_attempts + 1):
            try:
                future = self._master.submit_command(
                    node_id,
                    CommandPayload(command_id),
                )
                result = future.result(self._timing.command_timeout_s)
                if not result.succeeded or result.response is None:
                    raise RuntimeError(result.error or "no FleetBus response")
                if result.response.kind != int(MessageKind.ACK):
                    raise RuntimeError(
                        "{} returned a non-ACK response".format(command_id.name)
                    )
                ack = decode_ack(result.response.payload)
                if ack.status not in (
                    int(AckStatus.ACCEPTED),
                    int(AckStatus.COMPLETED),
                ):
                    raise RuntimeError(
                        "{} rejected: status={} reason={}".format(
                            command_id.name,
                            ack.status,
                            ack.reason,
                        )
                    )
                return result.request.seq
            except Exception as exc:
                error = exc
                LOG.warning(
                    "%s attempt %s/%s failed: %s",
                    command_id.name,
                    attempt,
                    self._timing.command_attempts,
                    exc,
                )
                if attempt < self._timing.command_attempts:
                    self._sleep(self._timing.command_retry_interval_s)
        raise RuntimeError(
            "{} failed after retries".format(command_id.name)
        ) from error

    def _sleep(self, duration):
        if self._wait is None:
            return self._stop.wait(duration)
        return self._wait(duration)


__all__ = ["RoadPatrolCoordinator", "RoadPatrolTiming"]
