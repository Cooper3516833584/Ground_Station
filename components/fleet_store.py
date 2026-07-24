"""Thread-safe ground-station view of FleetBus node state."""

from dataclasses import replace
import math
import threading
import time
from typing import Dict

from .fleet_models import (
    FleetSnapshot,
    LinkStatus,
    MessageKind,
    NodeFlags,
    NodeId,
    NodeSnapshot,
    WorldPose,
)
from .fleet_protocol import (
    ProtocolError,
    decode_ack,
    decode_map_report,
    decode_path_report,
    decode_report,
)
from .trajectory_store import TrajectoryStore


class FleetStore:
    def __init__(
        self,
        stale_seconds: float = 1.5,
        offline_after_missed_polls: int = 3,
        max_pose_jump_cm: float = 500.0,
    ) -> None:
        self._stale_seconds = stale_seconds
        self._offline_after_missed = offline_after_missed_polls
        self._max_pose_jump_cm = max_pose_jump_cm
        self._nodes = {
            int(NodeId.DRONE): NodeSnapshot(int(NodeId.DRONE)),
            int(NodeId.CAR): NodeSnapshot(int(NodeId.CAR)),
        }  # type: Dict[int, NodeSnapshot]
        self.trajectories = TrajectoryStore(self._nodes)
        self._lock = threading.Lock()

    def handle_frame(self, frame) -> None:
        if frame.src not in self._nodes:
            return
        try:
            if frame.kind == int(MessageKind.REPORT):
                self._handle_report(frame)
            elif frame.kind == int(MessageKind.ACK):
                self._handle_ack(frame)
            elif frame.kind == int(MessageKind.MAP_REPORT):
                self._handle_map(frame)
            elif frame.kind == int(MessageKind.PATH_REPORT):
                self._handle_path(frame)
        except ProtocolError:
            return

    def mark_timeout(self, node_id: int) -> None:
        node_id = int(node_id)
        with self._lock:
            previous = self._nodes[node_id]
            missed = previous.missed_polls + 1
            self._nodes[node_id] = replace(
                previous,
                missed_polls=missed,
                stale=True,
                online=previous.online and missed < self._offline_after_missed,
                link_status=(
                    LinkStatus.OFFLINE
                    if missed >= self._offline_after_missed
                    else LinkStatus.STALE
                ),
            )

    def mark_link_down(self) -> None:
        with self._lock:
            for node_id, previous in self._nodes.items():
                self._nodes[node_id] = replace(
                    previous,
                    online=False,
                    stale=True,
                    link_status=LinkStatus.OFFLINE,
                )

    def snapshot(self) -> FleetSnapshot:
        now = time.monotonic()
        with self._lock:
            nodes = {}
            for node_id, previous in self._nodes.items():
                stale = (
                    not previous.online
                    or now - previous.last_seen_monotonic > self._stale_seconds
                )
                nodes[node_id] = replace(
                    previous,
                    stale=stale,
                    link_status=(
                        LinkStatus.STALE
                        if stale and previous.online
                        else previous.link_status
                    ),
                )
        trajectories = tuple(
            (node_id, points)
            for node_id, points in self.trajectories.snapshot().items()
        )
        return FleetSnapshot(
            nodes[int(NodeId.DRONE)], nodes[int(NodeId.CAR)], trajectories
        )

    def _base_update(self, frame):
        previous = self._nodes[frame.src]
        session_changed = (
            previous.session is not None and previous.session != frame.session
        )
        if session_changed:
            previous = NodeSnapshot(frame.src)
            self.trajectories.clear(frame.src)
        return previous, time.monotonic()

    def _handle_report(self, frame) -> None:
        report = decode_report(frame.payload)
        with self._lock:
            previous, now = self._base_update(frame)
            errors = previous.errors
            if (
                previous.report is not None
                and previous.report.node_flags & int(NodeFlags.POSE_VALID)
                and report.node_flags & int(NodeFlags.POSE_VALID)
                and math.hypot(
                    report.x_cm - previous.report.x_cm,
                    report.y_cm - previous.report.y_cm,
                )
                > self._max_pose_jump_cm
            ):
                errors = (
                    errors
                    + (
                        "pose jump exceeds {:.0f} cm".format(
                            self._max_pose_jump_cm
                        ),
                    )
                )[-20:]
            updated = replace(
                previous,
                online=True,
                stale=False,
                link_status=LinkStatus.ONLINE,
                session=frame.session,
                last_seen=now,
                last_seen_monotonic=now,
                missed_polls=0,
                node_flags=report.node_flags,
                node_uptime_ms=report.node_uptime_ms,
                x_cm=report.x_cm,
                y_cm=report.y_cm,
                z_cm=report.z_cm,
                heading_cdeg=report.heading_cdeg,
                vx_cm_s=report.vx_cm_s,
                vy_cm_s=report.vy_cm_s,
                vz_cm_s=report.vz_cm_s,
                battery_cV=report.battery_cV,
                operation_state=report.operation_state,
                pose_quality=report.pose_quality,
                active_command_seq=report.active_command_seq,
                active_command_status=report.active_command_status,
                error_code=report.error_code,
                report=report,
                world_pose=WorldPose(
                    report.x_cm,
                    report.y_cm,
                    report.z_cm,
                    report.heading_cdeg / 100.0,
                    now,
                    report.pose_quality,
                ),
                errors=errors,
            )
            self._nodes[frame.src] = updated
        if report.node_flags & int(NodeFlags.POSE_VALID):
            self.trajectories.append(
                frame.src,
                report.x_cm,
                report.y_cm,
                report.z_cm,
                report.heading_cdeg / 100.0,
                report.pose_quality,
            )

    def _handle_ack(self, frame) -> None:
        ack = decode_ack(frame.payload)
        with self._lock:
            previous, now = self._base_update(frame)
            self._nodes[frame.src] = replace(
                previous,
                online=True,
                stale=False,
                link_status=LinkStatus.ONLINE,
                session=frame.session,
                last_seen=now,
                last_seen_monotonic=now,
                missed_polls=0,
                last_ack=ack,
            )

    def _handle_map(self, frame) -> None:
        report = decode_map_report(frame.payload)
        with self._lock:
            previous, now = self._base_update(frame)
            self._nodes[frame.src] = replace(
                previous,
                online=True,
                stale=False,
                link_status=LinkStatus.ONLINE,
                session=frame.session,
                last_seen=now,
                last_seen_monotonic=now,
                missed_polls=0,
                map_revision=report.map_revision,
                map_corners=report.corners,
            )

    def _handle_path(self, frame) -> None:
        report = decode_path_report(frame.payload)
        with self._lock:
            previous, now = self._base_update(frame)
            self._nodes[frame.src] = replace(
                previous,
                online=True,
                stale=False,
                link_status=LinkStatus.ONLINE,
                session=frame.session,
                last_seen=now,
                last_seen_monotonic=now,
                missed_polls=0,
                path_revision=report.path_revision,
                path_points=report.points,
            )
