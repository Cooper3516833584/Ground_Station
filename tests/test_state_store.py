import unittest

from models import FCState, MissionState, RejectReason
from state_store import StateStore


def sample_state() -> FCState:
    return FCState(
        roll_deg=0,
        pitch_deg=0,
        yaw_deg=0,
        alt_fused_cm=0,
        alt_add_cm=0,
        vel_x_cms=0,
        vel_y_cms=0,
        vel_z_cms=0,
        pos_x_cm=0,
        pos_y_cm=0,
        battery_v=12.0,
        mode=1,
        unlock=False,
        cid=0,
        cmd_0=0,
        cmd_1=0,
    )


class StateStoreTests(unittest.TestCase):
    def test_stale_after_1_5_seconds(self):
        store = StateStore(stale_after_seconds=1.5)
        store.update_telemetry(sample_state(), session=123, now=10.0)
        self.assertFalse(store.is_stale(now=11.5))
        self.assertTrue(store.is_stale(now=11.51))
        self.assertEqual(store.telemetry_age(now=11.0), 1.0)

    def test_start_rejected_when_stale_or_targets_missing(self):
        store = StateStore(stale_after_seconds=1.5)
        self.assertEqual(store.reject_reason_for_start(now=1.0), RejectReason.LINK_DOWN)
        store.update_telemetry(sample_state(), session=123, now=10.0)
        self.assertEqual(
            store.reject_reason_for_start(now=12.0), RejectReason.STALE_TELEMETRY
        )
        self.assertEqual(
            store.reject_reason_for_start(now=10.1), RejectReason.TARGETS_NOT_READY
        )
        store.mission.target1 = 1
        store.mission.target2 = 2
        self.assertEqual(store.reject_reason_for_start(now=10.1), RejectReason.NONE)
        store.mission.state = MissionState.RUNNING
        self.assertEqual(store.reject_reason_for_start(now=10.1), RejectReason.TASK_BUSY)


if __name__ == "__main__":
    unittest.main()
