"""Thread-safe ground-station view of FleetBus node state."""

from dataclasses import dataclass, replace
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
    TraceReportFlags,
    TraceSampleFlags,
    WorldPose,
)
from .fleet_protocol import (
    ProtocolError,
    decode_ack,
    decode_map_report,
    decode_path_report,
    decode_report,
    decode_survey_report,
    decode_trace_report,
)
from .trace_sync import TraceClockState, TraceCursorSnapshot
from .trajectory_store import TrajectoryStore


_DRONE_DISPATCHER_STATES = frozenset((30, 31, 32))


@dataclass
class _TraceState:
    trace_session: int = 0
    last_sample_seq: int = 0
    more_pending: bool = False
    active: bool = False
    consecutive_failures: int = 0
    buffer_overruns: int = 0
    sequence_gaps: int = 0
    clock: Optional[TraceClockState] = None


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
        self._trace_states = {
            node_id: _TraceState() for node_id in self._nodes
        }
        self._drone_task_session = None
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
            elif frame.kind == int(MessageKind.TRACE_REPORT):
                self._handle_trace_report(frame)
        except ProtocolError:
            return

    def mark_timeout(self, node_id: int) -> None:
        node_id = int(node_id)
        became_offline = False
        with self._lock:
            previous = self._nodes[node_id]
            missed = previous.missed_polls + 1
            updated = replace(
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
            self._nodes[node_id] = updated
            became_offline = previous.online and not updated.online
        if became_offline:
            self.trajectories.begin_new_segment(node_id)

    def mark_link_down(self) -> None:
        with self._lock:
            for node_id, previous in self._nodes.items():
                self._nodes[node_id] = replace(
                    previous,
                    online=False,
                    stale=True,
                    link_status=LinkStatus.OFFLINE,
                )
        for node_id in self._nodes:
            self.trajectories.begin_new_segment(node_id)

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

    def node_online(self, node_id: int) -> bool:
        with self._lock:
            return bool(self._nodes[int(node_id)].online)

    def trace_cursor(self, node_id: int) -> TraceCursorSnapshot:
        with self._lock:
            state = self._trace_states[int(node_id)]
            return TraceCursorSnapshot(
                state.trace_session,
                state.last_sample_seq,
                state.more_pending,
                state.active,
                state.consecutive_failures,
                state.buffer_overruns,
                state.sequence_gaps,
            )

    def note_trace_failure(self, node_id: int) -> None:
        with self._lock:
            self._trace_states[int(node_id)].consecutive_failures += 1

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
            self._trace_states[node_id] = _TraceState()

    def _base_update(self, frame):
        previous = self._nodes[frame.src]
        session_changed = (
            previous.session is not None and previous.session != frame.session
        )
        if session_changed:
            previous = NodeSnapshot(frame.src)
            if frame.src != int(NodeId.DRONE):
                self.trajectories.clear(frame.src)
            self._trace_states[frame.src] = _TraceState()
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
            if frame.src == int(NodeId.DRONE):
                if report.operation_state in _DRONE_DISPATCHER_STATES:
                    self._drone_task_session = None
                elif self._drone_task_session != frame.session:
                    self.trajectories.clear(frame.src)
                    self._drone_task_session = frame.session
            previous, now = self._base_update(frame)
            errors = previous.errors
            force_new_segment = False
            previous_pose_valid = bool(
                previous.report is not None
                and previous.report.node_flags & int(NodeFlags.POSE_VALID)
            )
            current_pose_valid = bool(
                report.node_flags & int(NodeFlags.POSE_VALID)
            )
            if (
                previous_pose_valid
                and current_pose_valid
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
                force_new_segment = True
            if (
                not previous_pose_valid
                and current_pose_valid
                and self.trajectories.has_points(frame.src)
            ):
                force_new_segment = True
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
            if updated.frame_valid and not self._trace_states[frame.src].active:
                world_pose = updated.world_pose
                self.trajectories.append(
                    frame.src,
                    world_pose.x_cm,
                    world_pose.y_cm,
                    world_pose.z_cm,
                    world_pose.heading_deg,
                    world_pose.quality,
                    force_new_segment=force_new_segment,
                    source="report",
                )

    def _handle_trace_report(self, frame) -> None:
        report = decode_trace_report(frame.payload)
        received_wall_time = time.time()
        with self._lock:
            previous = self._nodes[frame.src]
            if previous.session is not None and previous.session != frame.session:
                self._nodes[frame.src] = NodeSnapshot(frame.src)
                if frame.src == int(NodeId.DRONE):
                    self._drone_task_session = None
                else:
                    self.trajectories.clear(frame.src)
                self._trace_states[frame.src] = _TraceState()

            state = self._trace_states[frame.src]
            was_active = state.active
            session_changed = state.trace_session != report.trace_session
            if session_changed:
                if state.active and self.trajectories.has_points(frame.src):
                    self.trajectories.begin_new_segment(frame.src)
                state.trace_session = report.trace_session
                state.last_sample_seq = 0
                state.more_pending = False
                state.clock = None

            state.more_pending = bool(
                report.report_flags & int(TraceReportFlags.MORE_PENDING)
            )
            state.consecutive_failures = 0
            if not report.samples:
                return

            is_drone = frame.src == int(NodeId.DRONE)
            just_activated = not state.active
            if just_activated and not is_drone:
                self.trajectories.clear(frame.src)
                state.active = True

            if report.report_flags & int(TraceReportFlags.BUFFER_OVERRUN):
                if state.active or not is_drone:
                    self.trajectories.begin_new_segment(frame.src)
                state.buffer_overruns += 1
            if (
                report.report_flags & int(TraceReportFlags.CURSOR_RESET)
                and was_active
                and self.trajectories.has_points(frame.src)
            ):
                self.trajectories.begin_new_segment(frame.src)

            new_samples = []
            for index, sample in enumerate(report.samples):
                sample_seq = report.first_sample_seq + index
                if sample_seq > state.last_sample_seq:
                    new_samples.append((sample_seq, sample))
            if not new_samples:
                return

            if state.clock is None or state.clock.trace_session != report.trace_session:
                latest_sample = report.samples[-1]
                state.clock = TraceClockState(
                    report.trace_session,
                    latest_sample.uptime_ms,
                    received_wall_time,
                    0,
                )
            elif (
                state.last_sample_seq > 0
                and new_samples[0][1].uptime_ms <= state.clock.last_uptime_ms
            ):
                if state.active or not is_drone:
                    self.trajectories.begin_new_segment(frame.src)
                node = self._nodes[frame.src]
                self._nodes[frame.src] = replace(
                    node,
                    errors=(node.errors + ("trace uptime moved backwards",))[-20:],
                )
                latest_sample = report.samples[-1]
                state.clock = TraceClockState(
                    report.trace_session,
                    latest_sample.uptime_ms,
                    received_wall_time,
                    0,
                )

            transform = self._frame_registry.get(frame.src)
            for sample_seq, sample in new_samples:
                if (
                    state.last_sample_seq > 0
                    and sample_seq > state.last_sample_seq + 1
                ):
                    state.sequence_gaps += 1
                    if state.active or not is_drone:
                        self.trajectories.begin_new_segment(frame.src)

                timestamp = (
                    state.clock.anchor_wall_time
                    + (sample.uptime_ms - state.clock.anchor_uptime_ms) / 1000.0
                )
                valid_pose = bool(
                    sample.flags & int(TraceSampleFlags.POSE_VALID)
                )
                if not valid_pose:
                    if state.active or not is_drone:
                        self.trajectories.begin_new_segment(frame.src)
                elif transform is not None:
                    x_cm, y_cm = transform.local_to_world_point(
                        sample.x_cm, sample.y_cm
                    )
                    heading_deg = transform.local_to_world_heading(
                        sample.heading_cdeg / 100.0
                    )
                    if not state.active:
                        self.trajectories.clear(frame.src)
                        state.active = True
                        if is_drone:
                            self._drone_task_session = frame.session
                    self.trajectories.append(
                        frame.src,
                        x_cm,
                        y_cm,
                        sample.z_cm,
                        heading_deg,
                        sample.quality,
                        timestamp=timestamp,
                        sample_seq=sample_seq,
                        device_uptime_ms=sample.uptime_ms,
                        source="trace",
                    )
                state.last_sample_seq = sample_seq
                state.clock.last_uptime_ms = sample.uptime_ms

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
