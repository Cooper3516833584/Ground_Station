import unittest
from types import SimpleNamespace

from components.road_patrol_status import (
    RoadPatrolDisplayState,
    RoadPatrolStage,
    operation_state_label,
    stage_for_operation_state,
)


class RoadPatrolStatusTests(unittest.TestCase):
    def test_operation_states_have_the_required_screen_labels(self):
        self.assertEqual("待机", operation_state_label(40))
        self.assertEqual("起飞", operation_state_label(41))
        self.assertEqual("巡线", operation_state_label(42))
        self.assertEqual("避障", operation_state_label(43))
        self.assertEqual("抛投物资", operation_state_label(44))
        self.assertEqual("抛投物资", operation_state_label(45))
        self.assertEqual("降落", operation_state_label(46))
        self.assertEqual("降落", operation_state_label(47))

    def test_delivery_and_released_are_one_visible_stage(self):
        self.assertIs(
            RoadPatrolStage.PAYLOAD_DELIVERY,
            stage_for_operation_state(44),
        )
        self.assertIs(
            RoadPatrolStage.PAYLOAD_DELIVERY,
            stage_for_operation_state(45),
        )

    def test_display_keeps_last_stage_across_stale_or_unknown_reports(self):
        display = RoadPatrolDisplayState()
        self.assertEqual("待机", display.label)

        display.update_snapshot(
            SimpleNamespace(
                drone=SimpleNamespace(
                    online=True,
                    stale=False,
                    operation_state=42,
                )
            )
        )
        self.assertEqual("巡线", display.label)

        for drone in (
            SimpleNamespace(online=False, stale=True, operation_state=0),
            SimpleNamespace(online=True, stale=True, operation_state=41),
            SimpleNamespace(online=True, stale=False, operation_state=999),
        ):
            display.update_snapshot(SimpleNamespace(drone=drone))
            self.assertEqual("巡线", display.label)


if __name__ == "__main__":
    unittest.main()
