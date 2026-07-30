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
    MessageKind,
    NodeFlags,
    NodeId,
)
from .fleet_protocol import decode_ack
from .led_control import GroundLedClient


LOG = logging.getLogger("mission1-coordinator")
CAR_MISSION1_REQUESTED = 13
DRONE_HOVERING = 4


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
        wait: Optional[Callable[[float], bool]] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._master = master
        self._snapshot_provider = snapshot_provider
        self._timing = timing
        self._led = GroundLedClient() if led_client is None else led_client
        self._buzzer = buzzer
        self._stop = threading.Event()
        self._wait = self._stop.wait if wait is None else wait
        self._monotonic = monotonic
        self._thread = None
        self._handled_car_session = None

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
        try:
            self._led.off()
        except Exception:
            LOG.exception("failed to turn off ground LED during shutdown")

    def _run(self):
        while not self._stop.is_set():
            snapshot = self._snapshot_provider()
            car = snapshot.car
            session = car.session
            if (
                car.online
                and session is not None
                and car.operation_state == CAR_MISSION1_REQUESTED
                and session != self._handled_car_session
            ):
                self._handled_car_session = session
                try:
                    self.run_sequence()
                except Exception:
                    LOG.exception(
                        "Mission 1 startup sequence failed for car session %s",
                        session,
                    )
            self._wait(0.1)

    def run_sequence(self):
        LOG.info("MISSION1 request received from car")
        self._sleep(self._timing.ground_notice_delay_s)
        self._ground_notice()

        self._wait_until(
            lambda snapshot: snapshot.drone.online,
            "drone online",
        )
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
        LOG.info("Mission 1 startup sequence completed; car start released")

    def _ground_notice(self):
        try:
            for index in range(3):
                self._led.solid((255, 0, 0), brightness=20)
                self._buzzer(self._timing.notice_on_s)
                self._led.off()
                if index < 2:
                    self._sleep(self._timing.notice_off_s)
        finally:
            self._led.off()

    def _send_command(self, node_id, command_id):
        future = self._master.submit_command(
            node_id,
            CommandPayload(command_id),
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
