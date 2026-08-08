"""Road-patrol operation states and presentation logic."""

from enum import Enum, IntEnum


class RoadPatrolOperationState(IntEnum):
    STANDBY = 40
    TAKEOFF = 41
    LINE_FOLLOWING = 42
    OBSTACLE_AVOIDANCE = 43
    PAYLOAD_DELIVERY = 44
    PAYLOAD_RELEASED = 45
    LANDING = 46
    FAULT = 47


class RoadPatrolStage(Enum):
    STANDBY = "待机"
    TAKEOFF = "起飞"
    LINE_FOLLOWING = "巡线"
    OBSTACLE_AVOIDANCE = "避障"
    PAYLOAD_DELIVERY = "抛投物资"
    LANDING = "降落"


_OPERATION_TO_STAGE = {
    RoadPatrolOperationState.STANDBY: RoadPatrolStage.STANDBY,
    RoadPatrolOperationState.TAKEOFF: RoadPatrolStage.TAKEOFF,
    RoadPatrolOperationState.LINE_FOLLOWING: RoadPatrolStage.LINE_FOLLOWING,
    RoadPatrolOperationState.OBSTACLE_AVOIDANCE: RoadPatrolStage.OBSTACLE_AVOIDANCE,
    RoadPatrolOperationState.PAYLOAD_DELIVERY: RoadPatrolStage.PAYLOAD_DELIVERY,
    RoadPatrolOperationState.PAYLOAD_RELEASED: RoadPatrolStage.PAYLOAD_DELIVERY,
    RoadPatrolOperationState.LANDING: RoadPatrolStage.LANDING,
    RoadPatrolOperationState.FAULT: RoadPatrolStage.LANDING,
}


def stage_for_operation_state(value):
    try:
        state = RoadPatrolOperationState(int(value))
    except (TypeError, ValueError):
        return None
    return _OPERATION_TO_STAGE[state]


def operation_state_label(value, default=RoadPatrolStage.STANDBY.value):
    stage = stage_for_operation_state(value)
    return default if stage is None else stage.value


class RoadPatrolDisplayState:
    """Keep the last valid stage while a FleetBus report is temporarily stale."""

    def __init__(self):
        self._stage = RoadPatrolStage.STANDBY

    @property
    def stage(self):
        return self._stage

    @property
    def label(self):
        return self._stage.value

    def update_snapshot(self, snapshot):
        drone = snapshot.drone
        if drone.online and not drone.stale:
            stage = stage_for_operation_state(drone.operation_state)
            if stage is not None:
                self._stage = stage
        return self._stage


__all__ = [
    "RoadPatrolDisplayState",
    "RoadPatrolOperationState",
    "RoadPatrolStage",
    "operation_state_label",
    "stage_for_operation_state",
]
