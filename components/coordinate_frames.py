"""Pure SE(2) transforms between node-local frames and the FIELD frame."""

from dataclasses import dataclass
import math
from numbers import Integral, Real
import threading
from typing import Mapping, Optional, Tuple

from .fleet_models import NodeId


def _finite_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("{} must be a finite number".format(name))
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("{} must be finite".format(name))
    return number


def _positive_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError("revision must be a positive integer")
    revision = int(value)
    if revision <= 0:
        raise ValueError("revision must be a positive integer")
    return revision


def _node_id(value: object) -> int:
    if isinstance(value, str):
        names = {
            "drone": int(NodeId.DRONE),
            "car": int(NodeId.CAR),
        }
        try:
            return names[value.strip().lower()]
        except KeyError as exc:
            raise ValueError("unknown coordinate-frame node: {}".format(value)) from exc
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError("node_id must be an integer")
    return int(value)


@dataclass(frozen=True)
class FrameTransform2D:
    """A fixed local-frame pose expressed in the FIELD frame."""

    origin_world_x_cm: float
    origin_world_y_cm: float
    local_x_heading_world_deg: float
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "origin_world_x_cm",
            _finite_number("origin_world_x_cm", self.origin_world_x_cm),
        )
        object.__setattr__(
            self,
            "origin_world_y_cm",
            _finite_number("origin_world_y_cm", self.origin_world_y_cm),
        )
        object.__setattr__(
            self,
            "local_x_heading_world_deg",
            _finite_number(
                "local_x_heading_world_deg", self.local_x_heading_world_deg
            ),
        )
        object.__setattr__(self, "revision", _positive_revision(self.revision))

    @property
    def _theta_radians(self) -> float:
        return math.radians(self.local_x_heading_world_deg)

    def local_to_world_point(self, x_cm: float, y_cm: float) -> Tuple[float, float]:
        x, y = self.local_to_world_vector(x_cm, y_cm)
        return (
            self.origin_world_x_cm + x,
            self.origin_world_y_cm + y,
        )

    def world_to_local_point(self, x_cm: float, y_cm: float) -> Tuple[float, float]:
        x = _finite_number("x_cm", x_cm) - self.origin_world_x_cm
        y = _finite_number("y_cm", y_cm) - self.origin_world_y_cm
        return self.world_to_local_vector(x, y)

    def local_to_world_vector(self, x: float, y: float) -> Tuple[float, float]:
        x_value = _finite_number("x", x)
        y_value = _finite_number("y", y)
        cosine = math.cos(self._theta_radians)
        sine = math.sin(self._theta_radians)
        return (
            cosine * x_value - sine * y_value,
            sine * x_value + cosine * y_value,
        )

    def world_to_local_vector(self, x: float, y: float) -> Tuple[float, float]:
        x_value = _finite_number("x", x)
        y_value = _finite_number("y", y)
        cosine = math.cos(self._theta_radians)
        sine = math.sin(self._theta_radians)
        return (
            cosine * x_value + sine * y_value,
            -sine * x_value + cosine * y_value,
        )

    def local_to_world_heading(self, heading_deg: float) -> float:
        heading = _finite_number("heading_deg", heading_deg)
        return (heading + self.local_x_heading_world_deg) % 360.0

    def world_to_local_heading(self, heading_deg: float) -> float:
        heading = _finite_number("heading_deg", heading_deg)
        return (heading - self.local_x_heading_world_deg) % 360.0


class CoordinateFrameRegistry:
    """Thread-safe registry of fixed transforms keyed by FleetBus node ID."""

    def __init__(self) -> None:
        self._frames = {}
        self._lock = threading.RLock()

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> "CoordinateFrameRegistry":
        if not isinstance(config, Mapping):
            raise TypeError("coordinate-frame configuration must be a mapping")
        configured_frames = config.get("coordinate_frames")
        if configured_frames is None:
            configured_frames = {
                name: config[name]
                for name in ("drone", "car")
                if name in config
            }
        if not isinstance(configured_frames, Mapping):
            raise TypeError("coordinate_frames must be a mapping")

        registry = cls()
        for name, value in configured_frames.items():
            node_id = _node_id(name)
            if not isinstance(value, Mapping):
                raise TypeError(
                    "coordinate frame for {} must be a mapping".format(name)
                )
            try:
                transform = FrameTransform2D(
                    origin_world_x_cm=value["origin_world_x_cm"],
                    origin_world_y_cm=value["origin_world_y_cm"],
                    local_x_heading_world_deg=value[
                        "local_x_heading_world_deg"
                    ],
                    revision=value.get("revision", 1),
                )
            except KeyError as exc:
                raise ValueError(
                    "coordinate frame for {} is missing {}".format(name, exc.args[0])
                ) from exc
            registry.set(node_id, transform)
        return registry

    def get(self, node_id: int) -> Optional[FrameTransform2D]:
        with self._lock:
            return self._frames.get(_node_id(node_id))

    def require(self, node_id: int) -> FrameTransform2D:
        normalized_node_id = _node_id(node_id)
        with self._lock:
            try:
                return self._frames[normalized_node_id]
            except KeyError as exc:
                raise KeyError(
                    "no coordinate frame for node {}".format(normalized_node_id)
                ) from exc

    def set(self, node_id: int, transform: FrameTransform2D) -> None:
        if not isinstance(transform, FrameTransform2D):
            raise TypeError("transform must be a FrameTransform2D")
        with self._lock:
            self._frames[_node_id(node_id)] = transform

    def remove(self, node_id: int) -> None:
        with self._lock:
            self._frames.pop(_node_id(node_id), None)

    def revision(self, node_id: int) -> int:
        transform = self.get(node_id)
        return 0 if transform is None else transform.revision
