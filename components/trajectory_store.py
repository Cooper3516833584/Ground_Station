"""Bounded, thread-safe FleetBus trajectory history and CSV export."""

import csv
from collections import deque
from dataclasses import dataclass
import math
from pathlib import Path
import threading
import time
from typing import Dict, Iterable, Optional, Tuple


@dataclass(frozen=True)
class TrajectoryPoint:
    timestamp: float
    x_cm: float
    y_cm: float
    z_cm: float
    heading_deg: float
    quality: int


class TrajectoryStore:
    def __init__(
        self,
        node_ids: Iterable[int],
        max_points: int = 18000,
        min_distance_cm: float = 1.0,
        max_silence_s: float = 1.0,
    ) -> None:
        self._points = {
            int(node_id): deque(maxlen=max_points) for node_id in node_ids
        }
        self._min_distance_cm = min_distance_cm
        self._max_silence_s = max_silence_s
        self._lock = threading.Lock()

    def append(
        self,
        node_id: int,
        x_cm: int,
        y_cm: int,
        z_cm: int = 0,
        heading_deg: float = 0.0,
        quality: int = 0,
        timestamp: Optional[float] = None,
    ) -> bool:
        point = TrajectoryPoint(
            time.time() if timestamp is None else timestamp,
            float(x_cm),
            float(y_cm),
            float(z_cm),
            float(heading_deg),
            int(quality),
        )
        with self._lock:
            points = self._points[int(node_id)]
            if points:
                previous = points[-1]
                distance = math.hypot(
                    point.x_cm - previous.x_cm, point.y_cm - previous.y_cm
                )
                if (
                    distance < self._min_distance_cm
                    and point.timestamp - previous.timestamp < self._max_silence_s
                ):
                    return False
            points.append(point)
        return True

    def clear(self, node_id: Optional[int] = None) -> None:
        with self._lock:
            targets = self._points.values() if node_id is None else (
                self._points[int(node_id)],
            )
            for points in targets:
                points.clear()

    def snapshot(self) -> Dict[int, Tuple[TrajectoryPoint, ...]]:
        with self._lock:
            return {
                node_id: tuple(points) for node_id, points in self._points.items()
            }

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
                            point.x_cm,
                            point.y_cm,
                            point.z_cm,
                            point.heading_deg,
                            point.quality,
                        )
                    )
                    count += 1
        return count
