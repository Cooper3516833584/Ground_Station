"""Bounded FIELD-frame FleetBus trajectory history and CSV export.

TrajectoryStore stores ground-station FIELD-frame points, not raw node-local
points.
"""

import csv
from collections import deque
from dataclasses import dataclass
import math
from pathlib import Path
import threading
import time
from typing import Dict, Iterable, Mapping, Optional, Tuple


@dataclass(frozen=True)
class TrajectoryPolicy:
    min_distance_cm: float = 1.0
    stationary_keepalive_s: float = 1.0
    max_gap_s: float = 1.5
    max_speed_cm_s: float = 500.0
    min_quality: int = 1


def trajectory_policy_from_config(
    ui_config: Mapping[str, object], node_name: str
) -> TrajectoryPolicy:
    """Load one node policy while accepting the legacy distance field."""
    if not isinstance(ui_config, Mapping):
        raise ValueError("ui configuration must be an object")

    def number(
        value: object, field_name: str, *, allow_zero: bool
    ) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or (float(value) < 0.0 if allow_zero else float(value) <= 0.0)
        ):
            comparison = "non-negative" if allow_zero else "positive"
            raise ValueError("{} must be a {} number".format(
                field_name, comparison
            ))
        return float(value)

    trajectory_config = ui_config.get("trajectory", {})
    if not isinstance(trajectory_config, Mapping):
        raise ValueError("ui.trajectory must be an object")
    node_config = trajectory_config.get(node_name, {})
    if not isinstance(node_config, Mapping):
        raise ValueError(
            "ui.trajectory.{} must be an object".format(node_name)
        )

    defaults = TrajectoryPolicy()
    prefix = "ui.trajectory.{}.".format(node_name)
    minimum_quality = node_config.get(
        "minimum_quality", defaults.min_quality
    )
    if (
        isinstance(minimum_quality, bool)
        or not isinstance(minimum_quality, int)
        or minimum_quality < 0
    ):
        raise ValueError(prefix + "minimum_quality must be a non-negative integer")
    return TrajectoryPolicy(
        min_distance_cm=number(
            node_config.get(
                "min_distance_cm",
                ui_config.get(
                    "trajectory_min_distance_cm",
                    defaults.min_distance_cm,
                ),
            ),
            prefix + "min_distance_cm",
            allow_zero=True,
        ),
        stationary_keepalive_s=number(
            node_config.get(
                "stationary_keepalive_seconds",
                defaults.stationary_keepalive_s,
            ),
            prefix + "stationary_keepalive_seconds",
            allow_zero=False,
        ),
        max_gap_s=number(
            node_config.get("max_gap_seconds", defaults.max_gap_s),
            prefix + "max_gap_seconds",
            allow_zero=False,
        ),
        max_speed_cm_s=number(
            node_config.get("max_speed_cm_s", defaults.max_speed_cm_s),
            prefix + "max_speed_cm_s",
            allow_zero=False,
        ),
        min_quality=minimum_quality,
    )


@dataclass(frozen=True)
class TrajectoryPoint:
    timestamp: float
    segment_id: int
    x_cm: float
    y_cm: float
    z_cm: float
    heading_deg: float
    quality: int
    sample_seq: Optional[int] = None
    device_uptime_ms: Optional[int] = None
    source: str = "report"


class TrajectoryStore:
    def __init__(
        self,
        node_ids: Iterable[int],
        max_points: int = 18000,
        policies: Optional[Mapping[int, TrajectoryPolicy]] = None,
    ) -> None:
        node_ids = tuple(int(node_id) for node_id in node_ids)
        self._points = {
            node_id: deque(maxlen=max_points) for node_id in node_ids
        }
        supplied_policies = {} if policies is None else policies
        self._policies = {
            node_id: supplied_policies.get(node_id, TrajectoryPolicy())
            for node_id in node_ids
        }
        self._segment_ids = {node_id: 0 for node_id in node_ids}
        self._force_new_segment = {node_id: False for node_id in node_ids}
        self._lock = threading.Lock()

    def begin_new_segment(self, node_id: int) -> None:
        with self._lock:
            self._force_new_segment[int(node_id)] = True

    def append(
        self,
        node_id: int,
        x_cm: int,
        y_cm: int,
        z_cm: int = 0,
        heading_deg: float = 0.0,
        quality: int = 0,
        timestamp: Optional[float] = None,
        force_new_segment: bool = False,
        sample_seq: Optional[int] = None,
        device_uptime_ms: Optional[int] = None,
        source: str = "report",
    ) -> bool:
        node_id = int(node_id)
        point_timestamp = time.time() if timestamp is None else float(timestamp)
        point_quality = int(quality)
        with self._lock:
            points = self._points[node_id]
            policy = self._policies[node_id]
            if points:
                previous = points[-1]
                distance_cm = math.hypot(
                    float(x_cm) - previous.x_cm,
                    float(y_cm) - previous.y_cm,
                )
                dt = point_timestamp - previous.timestamp
                if (
                    distance_cm < policy.min_distance_cm
                    and dt >= 0.0
                    and dt < policy.stationary_keepalive_s
                    and previous.quality >= policy.min_quality
                    and point_quality >= policy.min_quality
                    and not force_new_segment
                    and not self._force_new_segment[node_id]
                ):
                    return False
                should_break = (
                    force_new_segment
                    or self._force_new_segment[node_id]
                    or dt <= 0.0
                    or dt > policy.max_gap_s
                    or previous.quality < policy.min_quality
                    or point_quality < policy.min_quality
                )
                if (
                    dt > 0.0
                    and distance_cm / dt > policy.max_speed_cm_s
                ):
                    should_break = True
                if should_break:
                    self._segment_ids[node_id] += 1
            point = TrajectoryPoint(
                point_timestamp,
                self._segment_ids[node_id],
                float(x_cm),
                float(y_cm),
                float(z_cm),
                float(heading_deg),
                point_quality,
                sample_seq,
                device_uptime_ms,
                str(source),
            )
            points.append(point)
            self._force_new_segment[node_id] = False
        return True

    def clear(self, node_id: Optional[int] = None) -> None:
        with self._lock:
            target_ids = (
                tuple(self._points)
                if node_id is None
                else (int(node_id),)
            )
            for target_id in target_ids:
                self._points[target_id].clear()
                self._segment_ids[target_id] = 0
                self._force_new_segment[target_id] = False

    def snapshot(self) -> Dict[int, Tuple[TrajectoryPoint, ...]]:
        with self._lock:
            return {
                node_id: tuple(points) for node_id, points in self._points.items()
            }

    def has_points(self, node_id: int) -> bool:
        with self._lock:
            return bool(self._points[int(node_id)])

    def export_csv(self, path: str) -> int:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        snapshot = self.snapshot()
        count = 0
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                (
                    "timestamp",
                    "node",
                    "segment_id",
                    "sample_seq",
                    "device_uptime_ms",
                    "source",
                    "x_cm",
                    "y_cm",
                    "z_cm",
                    "heading_deg",
                    "pose_quality",
                )
            )
            for node_id, points in snapshot.items():
                for point in points:
                    writer.writerow(
                        (
                            point.timestamp,
                            node_id,
                            point.segment_id,
                            point.sample_seq,
                            point.device_uptime_ms,
                            point.source,
                            point.x_cm,
                            point.y_cm,
                            point.z_cm,
                            point.heading_deg,
                            point.quality,
                        )
                    )
                    count += 1
        return count
