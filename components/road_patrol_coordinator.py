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
    car_start_timeout_s: float = 60.0
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
            car_start_timeout_s=float(
                value.get(
                    "car_start_timeout_seconds",
                    value.get("car_ready_timeout_seconds", 60.0),
                )
            ),
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
            self.car_start_timeout_s,
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
        self._takeoff_queued_sessions = set()
        self._takeoff_completed_sessions = set()
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
                release_entered = (
                    operation_state
                    == int(RoadPatrolOperationState.PAYLOAD_RELEASED)
                    and drone.session not in self._release_seen_sessions
                )
                if release_entered:
                    self._release_seen_sessions.add(drone.session)
            else:
                previous_operation_state = self._last_operation_state
                previous_stage = self._last_stage
                if operation_state == previous_operation_state:
                    stage_changed = False
                    release_entered = False
                else:
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
                and drone.session not in self._takeoff_queued_sessions
                and drone.session not in self._takeoff_completed_sessions
            )
            if takeoff_entered:
                self._takeoff_queued_sessions.add(drone.session)

        if stage_changed and not takeoff_entered:
            self._cue_queue.put(stage)
        if release_entered:
            LOG.info("payload release detected session=%s", drone.session)
            self._car_start_queue.put(drone.session)
        if takeoff_entered:
            LOG.info("road-patrol TAKEOFF detected session=%s", drone.session)
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
            succeeded = False
            try:
                succeeded = self._run_takeoff_sequence(drone_session)
            except Exception:
                LOG.exception(
                    "ground-controlled takeoff sequence failed for drone session %s",
                    drone_session,
                )
            finally:
                self._finish_takeoff_attempt(drone_session, succeeded)

    def _finish_takeoff_attempt(self, drone_session, succeeded):
        with self._lock:
            self._takeoff_queued_sessions.discard(drone_session)
            if succeeded:
                self._takeoff_completed_sessions.add(drone_session)

    def _run_takeoff_sequence(self, drone_session):
        if not self._continue_takeoff(drone_session, "before stage cue"):
            return False
        LOG.info("starting ground-controlled takeoff session=%s", drone_session)
        self._cue_player.play_stage_transition(
            on_seconds=self._timing.cue_on_s,
            off_seconds=self._timing.cue_off_s,
        )
        if not self._continue_takeoff(drone_session, "after stage cue"):
            return False

        prepare_seq = self._send_command(
            NodeId.DRONE,
            CommandId.DRONE_PREPARE_MISSION,
        )
        LOG.info(
            "DRONE_PREPARE_MISSION accepted session=%s seq=%s",
            drone_session,
            prepare_seq,
        )
        if not self._wait_drone_command_completion(
            drone_session,
            prepare_seq,
            "DRONE_PREPARE_MISSION",
            timeout_s=self._timing.takeoff_prepare_timeout_s,
        ):
            return False
        LOG.info(
            "DRONE_PREPARE_MISSION completed session=%s seq=%s",
            drone_session,
            prepare_seq,
        )

        LOG.info(
            "takeoff ready countdown started duration=%s",
            self._timing.takeoff_ready_wait_s,
        )
        if not self._wait_while_takeoff_pending(
            drone_session,
            self._timing.takeoff_ready_wait_s,
        ):
            return False
        LOG.info("takeoff ready countdown completed")
        LOG.info(
            "ground takeoff warning started duration=%s",
            self._timing.takeoff_warning_s,
        )
        self._cue_player.play_takeoff_warning(
            duration_seconds=self._timing.takeoff_warning_s,
        )
        if not self._continue_takeoff(drone_session, "after takeoff warning"):
            return False
        start_seq = self._send_command(
            NodeId.DRONE,
            CommandId.DRONE_START_MISSION,
        )
        LOG.info(
            "DRONE_START_MISSION accepted session=%s seq=%s",
            drone_session,
            start_seq,
        )
        if not self._wait_drone_command_completion(
            drone_session,
            start_seq,
            "DRONE_START_MISSION",
            timeout_s=self._timing.command_timeout_s,
        ):
            return False
        LOG.info(
            "DRONE_START_MISSION completed session=%s seq=%s; aircraft takeoff authorized",
            drone_session,
            start_seq,
        )
        return True

    def _wait_drone_command_completion(
        self, drone_session, command_seq, description, *, timeout_s=None
    ):
        timeout_s = self._timing.command_timeout_s if timeout_s is None else timeout_s
        deadline = self._monotonic() + timeout_s
        while True:
            snapshot = self._snapshot_provider()
            drone = snapshot.drone
            if not self._continue_takeoff(
                drone_session,
                "while waiting for {} completion".format(description),
                snapshot,
            ):
                return False
            if drone.active_command_seq == command_seq:
                status = int(drone.active_command_status)
                if status == int(AckStatus.COMPLETED):
                    return True
                if status == int(AckStatus.FAILED):
                    raise RuntimeError(
                        "{} failed session={} seq={} status={} error_code={} operation_state={}".format(
                            description,
                            drone_session,
                            command_seq,
                            status,
                            getattr(drone, "error_code", 0),
                            drone.operation_state,
                        )
                    )
            if self._monotonic() >= deadline:
                raise TimeoutError(
                    "timed out waiting for {} completion session={} seq={}".format(
                        description, drone_session, command_seq
                    )
                )
            self._sleep(min(0.1, max(0.0, deadline - self._monotonic())))

    def _wait_while_takeoff_pending(self, drone_session, duration):
        deadline = self._monotonic() + duration
        while True:
            if not self._continue_takeoff(
                drone_session,
                "during ready countdown",
            ):
                return False
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return True
            self._sleep(min(0.1, remaining))

    def _drone_waiting_for_takeoff(self, drone_session, snapshot=None):
        return self._takeoff_abort_reason(drone_session, snapshot) is None

    def _continue_takeoff(self, drone_session, checkpoint, snapshot=None):
        reason = self._takeoff_abort_reason(drone_session, snapshot)
        if reason is None:
            return True
        LOG.warning(
            "ground-controlled takeoff canceled for drone session %s at %s: %s",
            drone_session,
            checkpoint,
            reason,
        )
        return False

    def _takeoff_abort_reason(self, drone_session, snapshot=None):
        if self._stop.is_set():
            return "coordinator is stopping"
        current = self._snapshot_provider() if snapshot is None else snapshot
        drone = current.drone
        if drone.session != drone_session:
            return "drone session changed from {} to {}".format(
                drone_session,
                drone.session,
            )
        if not drone.online:
            missed_polls = getattr(drone, "missed_polls", None)
            detail = (
                ""
                if missed_polls is None
                else " after {} missed polls".format(missed_polls)
            )
            return "drone went offline{}".format(detail)
        if drone.operation_state != int(RoadPatrolOperationState.TAKEOFF):
            return "drone operation state changed from TAKEOFF to {}".format(
                drone.operation_state
            )
        return None

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
        deadline = self._monotonic() + self._timing.car_start_timeout_s
        last_error = None
        LOG.info("payload release detected; starting car session=%s", drone_session)
        while not self._stop.is_set():
            with self._lock:
                if self._drone_session != drone_session:
                    LOG.info(
                        "discarded car start from superseded drone session %s",
                        drone_session,
                    )
                    return False
            try:
                self._send_command(NodeId.CAR, CommandId.CAR_START_MISSION)
                LOG.info("CAR_START_MISSION completed")
                return True
            except Exception as exc:
                last_error = exc
            if self._monotonic() >= deadline:
                raise TimeoutError("timed out starting road-patrol car") from last_error
            LOG.warning(
                "CAR_START_MISSION not accepted yet; retrying: %s",
                last_error,
            )
            self._sleep(
                min(
                    self._timing.command_retry_interval_s,
                    max(0.0, deadline - self._monotonic()),
                )
            )
        return False

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
