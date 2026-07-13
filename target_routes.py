from __future__ import annotations

from dataclasses import dataclass


WAREHOUSE_WIDTH_CM = 500.0
WAREHOUSE_HEIGHT_CM = 400.0

START_POINT = (75.0, 75.0)
LANDING_POINT = (425.0, 325.0)
TOP_CORRIDOR_Y = 330.0

SHELVES = (
    (150.0, 100.0, 300.0),
    (350.0, 100.0, 300.0),
)

FACE_X = {
    "A": 120.0,
    "B": 180.0,
    "C": 320.0,
    "D": 380.0,
}

FACE_NAMES = {
    "A": "左侧货架 · A 面",
    "B": "左侧货架 · B 面",
    "C": "右侧货架 · C 面",
    "D": "右侧货架 · D 面",
}


Point = tuple[float, float]


@dataclass(frozen=True)
class TargetLocation:
    code: str
    face: str
    index: int
    column: int
    row: int
    point: Point
    height_cm: float

    @property
    def row_name(self) -> str:
        return "上排" if self.row == 0 else "下排"

    @property
    def face_name(self) -> str:
        return FACE_NAMES[self.face]


@dataclass(frozen=True)
class MissionRoute:
    target: TargetLocation
    outbound: tuple[Point, ...]
    return_path: tuple[Point, ...]

    @property
    def display_points(self) -> tuple[Point, ...]:
        return self.outbound + self.return_path[1:]


@dataclass(frozen=True)
class TargetMissionEvent:
    kind: str
    cargo_number: int | None = None
    location: str | None = None
    seconds: int | None = None
    detail: str = ""


def parse_location(code: str) -> TargetLocation:
    normalized = code.strip().upper()
    if len(normalized) != 2 or normalized[0] not in FACE_X:
        raise ValueError(f"invalid target location: {code!r}")
    try:
        index = int(normalized[1])
    except ValueError as exc:
        raise ValueError(f"invalid target location: {code!r}") from exc
    if not 1 <= index <= 6:
        raise ValueError(f"invalid target location: {code!r}")

    column = (index - 1) % 3
    row = (index - 1) // 3

    # A/C are viewed toward +X, so their left-to-right order is +Y to -Y.
    # B/D are viewed toward -X, which reverses that order in the top view.
    if normalized[0] in ("A", "C"):
        y = (250.0, 200.0, 150.0)[column]
    else:
        y = (150.0, 200.0, 250.0)[column]

    height_cm = 140.0 if row == 0 else 100.0
    return TargetLocation(
        code=normalized,
        face=normalized[0],
        index=index,
        column=column,
        row=row,
        point=(FACE_X[normalized[0]], y),
        height_cm=height_cm,
    )


def route_for(code: str) -> MissionRoute:
    target = parse_location(code)
    face_entry = (target.point[0], TOP_CORRIDOR_Y)
    return MissionRoute(
        target=target,
        outbound=(START_POINT, (START_POINT[0], TOP_CORRIDOR_Y), face_entry, target.point),
        return_path=(
            target.point,
            face_entry,
            (LANDING_POINT[0], TOP_CORRIDOR_Y),
            LANDING_POINT,
        ),
    )


def all_location_codes() -> tuple[str, ...]:
    return tuple(f"{face}{index}" for face in "ABCD" for index in range(1, 7))


def all_routes() -> tuple[MissionRoute, ...]:
    return tuple(route_for(code) for code in all_location_codes())


def cycle_location_codes(rounds: int) -> tuple[str, ...]:
    if rounds < 1:
        raise ValueError("rounds must be at least 1")
    return all_location_codes() * rounds


def parse_target_message(message: str) -> TargetMissionEvent | None:
    parts = message.strip().split(":")
    if len(parts) < 2 or parts[0] != "TGT":
        return None
    kind = parts[1].upper()
    if kind == "COUNTDOWN" and len(parts) == 5:
        try:
            seconds = int(parts[2])
            cargo_number = int(parts[3])
            location = parse_location(parts[4]).code
        except (ValueError, TypeError):
            return None
        if not 1 <= cargo_number <= 24 or seconds < 0:
            return None
        return TargetMissionEvent(kind, cargo_number, location, seconds)
    if kind in {
        "DETECTED",
        "TAKEOFF",
        "ARRIVED",
        "VERIFIED",
        "LANDING",
        "COMPLETE",
    } and len(parts) == 4:
        try:
            cargo_number = int(parts[2])
            location = parse_location(parts[3]).code
        except (ValueError, TypeError):
            return None
        if not 1 <= cargo_number <= 24:
            return None
        return TargetMissionEvent(kind, cargo_number, location)
    if kind == "FAILED" and len(parts) >= 3:
        return TargetMissionEvent(kind, detail=":".join(parts[2:]))
    return None
