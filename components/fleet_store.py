"""Thread-safe ground-station view of FleetBus node state."""

from dataclasses import replace
import math
import threading
import time
from typing import Dict, Optional

from .coordinate_frames import CoordinateFrameRegistry, FrameTransform2D
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
    decode_survey_report,
)
from .trajectory_store import TrajectoryStore


class FleetStore:
    def __init__(
        self,
        stale_seconds: float = 1.5,
        offline_after_missed_polls: int = 3,
        max_pose_jump_cm: float = 500.0,
        frame_registry: Optional[CoordinateFrameRegistry] = None,
    ) -> None:
        self._stale_seconds = stale_seconds
        self._offline_after_missed = offline_after_missed_polls
        self._max_pose_jump_cm = max_pose_jump_cm
        self._nodes = {
            int(NodeId.DRONE): NodeSnapshot(int(NodeId.DRONE)),
            int(NodeId.CAR): NodeSnapshot(int(NodeId.CAR)),
        }  # type: Dict[int, NodeSnapshot]
        self._frame_registry = (
            CoordinateFrameRegistry()
            if frame_registry is None
            else frame_registry
        )
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
            elif frame.kind == int(MessageKind.SURVEY_REPORT):
                self._handle_survey(frame)
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

    def frame_transform(self, node_id: int) -> Optional[FrameTransform2D]:
        return self._frame_registry.get(node_id)

    def set_frame_transform(
        self, node_id: int, transform: FrameTransform2D
    ) -> None:
        """Rebuild one node's FIELD presentation without radio side effects."""
        node_id = int(node_id)
        with self._lock:
            if node_id not in self._nodes:
                raise KeyError("unknown FleetBus node {}".format(node_id))
            self._frame_registry.set(node_id, transform)
            previous = self._nodes[node_id]
            self._nodes[node_id] = self._with_derived_world(previous, transform)
            self.trajectories.clear(node_id)

    def _base_update(self, frame):
        previous = self._nodes[frame.src]
        session_changed = (
            previous.session is not None and previous.session != frame.session
        )
        if session_changed:
            previous = NodeSnapshot(frame.src)
            self.trajectories.clear(frame.src)
        return previous, time.monotonic()

    @staticmethod
    def _world_points(transform, points):
        if transform is None:
            return ()
        return tuple(
            transform.local_to_world_point(x_cm, y_cm)
            for x_cm, y_cm in points
        )

    @staticmethod
    def _world_pose(report, transform, updated_at):
        if (
            report is None
            or transform is None
            or not report.node_flags & int(NodeFlags.POSE_VALID)
        ):
            return None
        x_cm, y_cm = transform.local_to_world_point(report.x_cm, report.y_cm)
        return WorldPose(
            x_cm,
            y_cm,
            report.z_cm,
            transform.local_to_world_heading(report.heading_cdeg / 100.0),
            updated_at,
            report.pose_quality,
        )

    def _with_derived_world(self, snapshot, transform):
        return replace(
            snapshot,
            frame_valid=bool(
                transform is not None
                and snapshot.report is not None
                and snapshot.report.node_flags & int(NodeFlags.POSE_VALID)
            ),
            frame_revision=0 if transform is None else transform.revision,
            world_pose=self._world_pose(
                snapshot.report,
                transform,
                snapshot.last_seen_monotonic,
            ),
            world_map_corners=self._world_points(
                transform, snapshot.map_corners
            ),
            world_path_points=self._world_points(transform, snapshot.path_points),
        )

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
                errors=errors,
            )
            updated = self._with_derived_world(
                updated, self._frame_registry.get(frame.src)
            )
            self._nodes[frame.src] = updated
            if updated.frame_valid:
                world_pose = updated.world_pose
                self.trajectories.append(
                    frame.src,
                    world_pose.x_cm,
                    world_pose.y_cm,
                    world_pose.z_cm,
                    world_pose.heading_deg,
                    world_pose.quality,
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
            self._nodes[frame.src] = self._with_derived_world(
                self._nodes[frame.src], self._frame_registry.get(frame.src)
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
            self._nodes[frame.src] = self._with_derived_world(
                self._nodes[frame.src], self._frame_registry.get(frame.src)
            )

    def _handle_survey(self, frame) -> None:
        report = decode_survey_report(frame.payload)
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
                survey_revision=report.survey_revision,
                survey_flags=report.survey_flags,
                wildfire_event_id=report.wildfire_event_id,
                wildfire_row=report.wildfire_row,
                wildfire_col=report.wildfire_col,
                debris_event_id=report.debris_event_id,
                debris_row=report.debris_row,
                debris_col=report.debris_col,
                terrain_codes=report.terrain_codes,
                survey_cell_positions_cm=report.cell_positions_cm,
            )
