import unittest

from components.d_task_status import (
    DTaskOperationState,
    drone_task_program_active,
    operation_state_label,
)


class DTaskStatusTests(unittest.TestCase):
    def test_dispatcher_idle_is_presented_as_standby(self):
        self.assertEqual("待机", operation_state_label(30))

    def test_required_flight_phases_have_operator_labels(self):
        self.assertEqual("无人机起飞", operation_state_label(DTaskOperationState.DRONE_TAKEOFF))
        self.assertEqual("无人机巡航", operation_state_label(DTaskOperationState.DRONE_CRUISING))
        self.assertEqual(
            "无人机复飞完成",
            operation_state_label(
                DTaskOperationState.DRONE_RETAKEOFF_SUCCEEDED
            ),
        )
        self.assertEqual("无人机伴飞", operation_state_label(DTaskOperationState.DRONE_ESCORTING))
        self.assertEqual(
            "任务一抛投完成",
            operation_state_label(DTaskOperationState.MISSION1_DROP_COMPLETED),
        )
        self.assertEqual("无人机抛投", operation_state_label(DTaskOperationState.DRONE_DROPPING))
        self.assertEqual("正在降落 H 点", operation_state_label(DTaskOperationState.DRONE_LANDING_HOME))

    def test_unknown_values_remain_visible_for_endpoint_integration(self):
        self.assertEqual("未定义状态 (77)", operation_state_label(77))
        self.assertEqual("未上报", operation_state_label(None))

    def test_car_uses_its_existing_state_values(self):
        self.assertEqual("小车循迹中", operation_state_label(4, "car"))
        self.assertEqual("小车已到达", operation_state_label(7, "car"))
        self.assertEqual(
            "任务一已请求，等待联调启动",
            operation_state_label(13, "car"),
        )

    def test_drone_task_program_activity_boundaries(self):
        self.assertFalse(drone_task_program_active(0, None))
        self.assertFalse(drone_task_program_active(30, 100))
        self.assertTrue(drone_task_program_active(0, 101))
        self.assertTrue(drone_task_program_active(5, 101))
        self.assertTrue(drone_task_program_active(77, 101))
        self.assertFalse(
            drone_task_program_active(DTaskOperationState.COMPLETED, 101)
        )
        self.assertFalse(
            drone_task_program_active(DTaskOperationState.STOPPED, 101)
        )
        self.assertFalse(
            drone_task_program_active(DTaskOperationState.FAULT, 101)
        )

if __name__ == "__main__":
    unittest.main()
