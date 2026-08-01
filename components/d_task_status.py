"""Display-only operation-state vocabulary for the land-air D task.

FleetBus V1 deliberately leaves ``ReportPayload.operation_state`` as a task
defined byte.  The two endpoints for this task should publish the values below;
the ground station only renders them and never changes task state from a report.
"""

import time


class DTaskOperationState:
    IDLE = 0
    READY = 1
    CAR_FOLLOWING_LINE = 2
    DRONE_TAKEOFF = 3
    DRONE_HOVERING = 4
    DRONE_ESCORTING = 5
    DRONE_DROPPING = 6
    DRONE_LANDING_ON_CAR = 7
    DRONE_ON_CAR = 8
    DRONE_RETURNING_HOME = 9
    DRONE_LANDING_HOME = 10
    COMPLETED = 11
    STOPPED = 12
    FAULT = 13
    MISSION1_DROP_COMPLETED = 14
    DRONE_CRUISING = 15


_DRONE_STATE_LABELS = {
    30: "待机",
    31: "调度器切换中",
    32: "调度器故障",
    DTaskOperationState.IDLE: "待机",
    DTaskOperationState.READY: "已就绪",
    DTaskOperationState.CAR_FOLLOWING_LINE: "小车循线中",
    DTaskOperationState.DRONE_TAKEOFF: "无人机起飞",
    DTaskOperationState.DRONE_HOVERING: "无人机悬停",
    DTaskOperationState.DRONE_ESCORTING: "无人机伴飞",
    DTaskOperationState.DRONE_DROPPING: "无人机抛投",
    DTaskOperationState.DRONE_LANDING_ON_CAR: "正在降落小车",
    DTaskOperationState.DRONE_ON_CAR: "已停留小车平台",
    DTaskOperationState.DRONE_RETURNING_HOME: "无人机返航",
    DTaskOperationState.DRONE_LANDING_HOME: "正在降落 H 点",
    DTaskOperationState.COMPLETED: "任务完成",
    DTaskOperationState.STOPPED: "任务已停止",
    DTaskOperationState.FAULT: "任务故障",
    DTaskOperationState.MISSION1_DROP_COMPLETED: "任务一抛投完成",
    DTaskOperationState.DRONE_CRUISING: "无人机巡航",
}

# These are the values currently published by the car's
# ``CarFleetStateProvider``.  They deliberately remain separate from the D-task
# drone phases above: both endpoints share the one-byte FleetBus field but do
# not share an operation-state enum.
_CAR_STATE_LABELS = {
    0: "小车启动中",
    1: "小车标定中",
    2: "小车已就绪",
    3: "小车路径规划中",
    4: "小车循迹中",
    5: "小车末段进近",
    6: "小车换挡中",
    7: "小车已到达",
    8: "小车暂停",
    9: "小车受阻",
    10: "小车定位丢失",
    11: "小车失败",
    12: "小车已关闭",
    13: "任务一已请求，等待联调启动",
    14: "任务二已请求，等待联调启动",
}

_DRONE_DISPATCHER_STATES = frozenset((30, 31, 32))
_DRONE_TERMINAL_STATES = frozenset(
    (
        DTaskOperationState.COMPLETED,
        DTaskOperationState.STOPPED,
        DTaskOperationState.FAULT,
    )
)
_CAR_MOVING_STATES = frozenset((4, 5, 6))
_CAR_ARRIVED = 7
_DRONE_MOVING_STATES = frozenset(range(2, 11)) | frozenset((14, 15))


class TaskElapsedTimer:
    """Measure one task from first reported movement until the car arrives."""

    def __init__(self, clock=None):
        self._clock = time.monotonic if clock is None else clock
        self._started_at = None
        self._elapsed_s = None
        self._car_moved = False
        self._activity_seen = False
        self._drone_session = None

    def update(self, car_state, drone_state, drone_session=None):
        car_moving = car_state in _CAR_MOVING_STATES
        activity = car_moving or drone_state in _DRONE_MOVING_STATES
        now = self._clock()

        if self._started_at is None:
            new_drone_task = (
                self._elapsed_s is not None
                and activity
                and drone_session is not None
                and self._drone_session is not None
                and drone_session != self._drone_session
            )
            initial_activity = (
                self._elapsed_s is None
                and car_state != _CAR_ARRIVED
                and not self._activity_seen
            )
            resumed_activity = (
                self._elapsed_s is not None and not self._activity_seen
            )
            if activity and (
                initial_activity or resumed_activity or new_drone_task
            ):
                self._started_at = now
                self._elapsed_s = None
                self._car_moved = car_moving
                self._drone_session = drone_session
            self._activity_seen = activity
        else:
            self._car_moved = self._car_moved or car_moving
            if self._car_moved and car_state == _CAR_ARRIVED:
                self._elapsed_s = max(0.0, now - self._started_at)
                self._started_at = None
            self._activity_seen = activity

        if self._started_at is not None:
            return max(0.0, now - self._started_at)
        return self._elapsed_s


def operation_state_label(value, node_role="drone"):
    """Return a stable Chinese label without rejecting future task values."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return "未上报"
    labels = _CAR_STATE_LABELS if node_role == "car" else _DRONE_STATE_LABELS
    return labels.get(value, "未定义状态 ({})".format(value))


def drone_task_program_active(operation_state, session):
    """Return whether the drone task process has started and not terminated."""
    if session is None:
        return False
    try:
        operation_state = int(operation_state)
    except (TypeError, ValueError):
        return False
    return operation_state not in (
        _DRONE_DISPATCHER_STATES | _DRONE_TERMINAL_STATES
    )
