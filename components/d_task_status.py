"""Display-only operation-state vocabulary for the land-air D task.

FleetBus V1 deliberately leaves ``ReportPayload.operation_state`` as a task
defined byte.  The two endpoints for this task should publish the values below;
the ground station only renders them and never changes task state from a report.
"""


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


_DRONE_STATE_LABELS = {
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
}


def operation_state_label(value, node_role="drone"):
    """Return a stable Chinese label without rejecting future task values."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return "未上报"
    labels = _CAR_STATE_LABELS if node_role == "car" else _DRONE_STATE_LABELS
    return labels.get(value, "未定义状态 ({})".format(value))
