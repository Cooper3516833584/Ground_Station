"""Ground-side Mission 1 startup coordinator for the D-task FleetBus."""

from dataclasses import dataclass
import logging
import threading
import time
from typing import Callable, Mapping, Optional

from .buzzer_control import trigger_buzzer
from .fleet_models import (
    AckStatus,
    CommandId,
    CommandPayload,
    MissionId,
    MessageKind,
    NodeFlags,
    NodeId,
)
from .fleet_protocol import decode_ack, encode_drone_select_mission
from .ground_cue_player import GroundCuePlayer


LOG = logging.getLogger("mission1-coordinator")
CAR_MISSION1_REQUESTED = 13
CAR_MISSION2_REQUESTED = 14
DRONE_HOVERING = 4
DISPATCHER_READY = 30


@dataclass(frozen=True)
class MissionSpec:
    mission_id: MissionId
    name: str


TASK_SPECS = {
    CAR_MISSION1_REQUESTED: MissionSpec(MissionId.MISSION1, "MISSION1"),
    CAR_MISSION2_REQUESTED: MissionSpec(MissionId.MISSION2, "MISSION2"),
}


@dataclass(frozen=True)
class Mission1Timing:
    ground_notice_delay_s: float = 3.0
    notice_on_s: float = 0.2
    notice_off_s: float = 0.2
    magnet_hold_s: float = 15.0
    car_alarm_s: float = 5.0
    endpoint_timeout_s: float = 60.0
    command_timeout_s: float = 10.0

    @classmethod
    def from_config(cls, value: Optional[Mapping[str, object]]):
        if not value:
            return cls()
        return cls(
            ground_notice_delay_s=float(value.get("ground_notice_delay_seconds", 3.0)),
            notice_on_s=float(value.get("notice_on_seconds", 0.2)),
            notice_off_s=float(value.get("notice_off_seconds", 0.2)),
            magnet_hold_s=float(value.get("magnet_hold_seconds", 15.0)),
            car_alarm_s=float(value.get("car_alarm_seconds", 5.0)),
            endpoint_timeout_s=float(value.get("endpoint_timeout_seconds", 60.0)),
            command_timeout_s=float(value.get("command_timeout_seconds", 10.0)),
        )

    def __post_init__(self):
        values = (
            self.ground_notice_delay_s,
            self.notice_on_s,
            self.notice_off_s,
            self.magnet_hold_s,
            self.car_alarm_s,
            self.endpoint_timeout_s,
            self.command_timeout_s,
        )
        if min(values) <= 0:
            raise ValueError("Mission 1 timing values must be positive")


class Mission1Coordinator:
    """Run the one-shot cross-endpoint startup sequence outside UI/serial threads."""

    def __init__(
        self,
        master,
        snapshot_provider: Callable,
        *,
        timing: Mission1Timing = Mission1Timing(),
        led_client=None,
        buzzer: Callable[[float], None] = trigger_buzzer,
        cue_player=None,
        wait: Optional[Callable[[float], bool]] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._master = master
        self._snapshot_provider = snapshot_provider
        self._timing = timing
        self._stop = threading.Event()
        self._wait = self._stop.wait if wait is None else wait
        self._monotonic = monotonic
        self._cue_player = (
            GroundCuePlayer(
                led=led_client,
                buzzer=buzzer,
                wait=self._sleep,
            )
            if cue_player is None
            else cue_player
        )
        self._thread = None
        self._handled_car_session = None
        self._last_request_state = None
        self._active = False
        self._mission_selected = False

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="mission1-coordinator",
            daemon=True,
        )
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._cue_player.turn_off()

    def _run(self):
        while not self._stop.is_set():
            snapshot = self._snapshot_provider()
            car = snapshot.car
            session = car.session
            request_state = car.operation_state if car.online else None
            entered_request = (
                request_state in TASK_SPECS
                and request_state != self._last_request_state
            )
            if (
                car.online
                and session is not None
                and entered_request
                and not self._active
            ):
                self._handled_car_session = session
                self._active = True
                try:
                    self.run_sequence(TASK_SPECS[request_state])
                except Exception:
                    LOG.exception(
                        "%s startup sequence failed for car session %s",
                        TASK_SPECS[request_state].name,
                        session,
                    )
                finally:
                    self._active = False
            self._last_request_state = request_state
            self._wait(0.1)

    def run_sequence(self, spec: MissionSpec = TASK_SPECS[CAR_MISSION1_REQUESTED]):
        self._mission_selected = False
        try:
            self._run_sequence(spec)
        except Exception:
            if self._mission_selected:
                self._best_effort_stop(NodeId.DRONE)
            self._best_effort_stop(NodeId.CAR)
            raise
        finally:
            self._mission_selected = False

    def _run_sequence(self, spec: MissionSpec):
        LOG.info("%s request received from car", spec.name)
        self._sleep(self._timing.ground_notice_delay_s)
        self._ground_notice()

        self._wait_until(
            lambda snapshot: (
                snapshot.drone.online
                and snapshot.drone.operation_state == DISPATCHER_READY
            ),
            "idle drone dispatcher",
        )
        dispatcher_snapshot = self._snapshot_provider().drone
        selection_error = None
        try:
            self._send_command(
                NodeId.DRONE,
                CommandId.DRONE_SELECT_MISSION,
                encode_drone_select_mission(spec.mission_id),
            )
        except Exception as exc:
            selection_error = exc
            LOG.warning(
                "%s select response was not conclusive; waiting for task session",
                spec.name,
            )
        try:
            self._wait_until(
                lambda snapshot: (
                    snapshot.drone.online
                    and snapshot.drone.session != dispatcher_snapshot.session
                    and snapshot.drone.operation_state != DISPATCHER_READY
                ),
                "selected drone mission online",
            )
        except Exception:
            if selection_error is not None:
                raise selection_error
            raise
        self._mission_selected = True
        if selection_error is not None:
            LOG.info(
                "%s selection confirmed by drone task session transition",
                spec.name,
            )
        LOG.info("%s selected on drone dispatcher", spec.name)
        LOG.info("%s drone task process is online", spec.name)
        prepare_seq = self._send_command(
            NodeId.DRONE,
            CommandId.DRONE_PREPARE_MISSION,
        )
        self._wait_until(
            lambda snapshot: (
                snapshot.drone.active_command_seq == prepare_seq
                and snapshot.drone.active_command_status
                == int(AckStatus.COMPLETED)
            ),
            "drone electromagnet preparation",
        )
        LOG.info("%s drone preparation completed", spec.name)

        self._sleep(self._timing.magnet_hold_s)
        self._send_command(NodeId.CAR, CommandId.CAR_ALARM_ON)
        try:
            self._sleep(self._timing.car_alarm_s)
        finally:
            self._send_command(NodeId.CAR, CommandId.CAR_ALARM_OFF)

        self._send_command(NodeId.DRONE, CommandId.DRONE_START_MISSION)
        self._wait_until(
            lambda snapshot: (
                snapshot.drone.online
                and snapshot.drone.operation_state == DRONE_HOVERING
            ),
            "drone cruise height",
        )
        self._wait_until(
            lambda snapshot: (
                snapshot.car.online
                and snapshot.car.node_flags & int(NodeFlags.READY)
            ),
            "car calibration",
        )
        self._send_command(NodeId.CAR, CommandId.CAR_START_MISSION)
        LOG.info("%s startup sequence completed; car start released", spec.name)

    def _best_effort_stop(self, node_id):
        try:
            self._send_command(node_id, CommandId.TARGETED_STOP)
        except Exception:
            LOG.exception("failed to stop node %s after coordination failure", node_id)

    def _ground_notice(self):
        self._cue_player.play_start_notice(
            on_seconds=self._timing.notice_on_s,
            off_seconds=self._timing.notice_off_s,
        )

    def _send_command(self, node_id, command_id, command_body=b""):
        future = self._master.submit_command(
            node_id,
            CommandPayload(command_id, command_body=command_body),
        )
        result = future.result(self._timing.command_timeout_s)
        if not result.succeeded or result.response is None:
            raise RuntimeError(
                "{} command failed: {}".format(command_id.name, result.error)
            )
        if result.response.kind != int(MessageKind.ACK):
            raise RuntimeError("{} returned a non-ACK response".format(command_id.name))
        ack = decode_ack(result.response.payload)
        if ack.status not in (int(AckStatus.ACCEPTED), int(AckStatus.COMPLETED)):
            raise RuntimeError(
                "{} rejected with status={} reason={}".format(
                    command_id.name,
                    ack.status,
                    ack.reason,
                )
            )
        return result.request.seq

    def _wait_until(self, predicate, description):
        deadline = self._monotonic() + self._timing.endpoint_timeout_s
        while not self._stop.is_set():
            if predicate(self._snapshot_provider()):
                return
            if self._monotonic() >= deadline:
                raise TimeoutError("timed out waiting for {}".format(description))
            self._wait(0.1)
        raise RuntimeError("Mission 1 coordinator stopped")

    def _sleep(self, duration):
        if self._wait(duration) or self._stop.is_set():
            raise RuntimeError("Mission 1 coordinator stopped")
