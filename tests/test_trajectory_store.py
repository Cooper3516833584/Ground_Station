import unittest

from components.trajectory_store import TrajectoryPolicy, TrajectoryStore


class TrajectoryStoreSegmentationTests(unittest.TestCase):
    def make_store(self, **policy_values):
        policy = TrajectoryPolicy(**policy_values)
        return TrajectoryStore((1, 2), policies={1: policy, 2: policy})

    def test_adjacent_stationary_point_is_deduplicated(self):
        store = self.make_store()
        self.assertTrue(store.append(1, 0, 0, quality=2, timestamp=0.0))
        self.assertFalse(store.append(1, 0, 0, quality=2, timestamp=0.5))

    def test_stationary_keepalive_is_retained_in_same_segment(self):
        store = self.make_store()
        store.append(1, 0, 0, quality=2, timestamp=0.0)
        self.assertTrue(store.append(1, 0, 0, quality=2, timestamp=1.1))
        points = store.snapshot()[1]
        self.assertEqual(points[0].segment_id, points[1].segment_id)

    def test_large_time_gap_starts_new_segment(self):
        store = self.make_store(max_gap_s=1.0)
        store.append(1, 0, 0, quality=2, timestamp=0.0)
        store.append(1, 10, 0, quality=2, timestamp=2.0)
        points = store.snapshot()[1]
        self.assertEqual(points[0].segment_id + 1, points[1].segment_id)

    def test_excessive_speed_starts_new_segment(self):
        store = self.make_store(max_speed_cm_s=100.0)
        store.append(1, 0, 0, quality=2, timestamp=0.0)
        store.append(1, 1000, 0, quality=2, timestamp=1.0)
        points = store.snapshot()[1]
        self.assertEqual(points[0].segment_id + 1, points[1].segment_id)

    def test_low_quality_point_breaks_before_and_after(self):
        store = self.make_store(min_quality=1)
        store.append(1, 0, 0, quality=2, timestamp=0.0)
        store.append(1, 10, 0, quality=0, timestamp=0.1)
        store.append(1, 20, 0, quality=2, timestamp=0.2)
        segment_ids = [point.segment_id for point in store.snapshot()[1]]
        self.assertEqual([0, 1, 2], segment_ids)

    def test_begin_new_segment_applies_to_next_accepted_point(self):
        store = self.make_store()
        store.append(1, 0, 0, quality=2, timestamp=0.0)
        store.begin_new_segment(1)
        store.append(1, 1, 0, quality=2, timestamp=0.1)
        points = store.snapshot()[1]
        self.assertEqual(points[0].segment_id + 1, points[1].segment_id)

    def test_force_new_segment_argument_breaks_connection(self):
        store = self.make_store()
        store.append(1, 0, 0, quality=2, timestamp=0.0)
        store.append(
            1,
            1,
            0,
            quality=2,
            timestamp=0.1,
            force_new_segment=True,
        )
        points = store.snapshot()[1]
        self.assertEqual(points[0].segment_id + 1, points[1].segment_id)

    def test_clear_resets_segment_id_and_pending_break(self):
        store = self.make_store()
        store.append(1, 0, 0, quality=2, timestamp=0.0)
        store.begin_new_segment(1)
        store.append(1, 1, 0, quality=2, timestamp=0.1)
        store.begin_new_segment(1)
        store.clear(1)
        store.append(1, 2, 0, quality=2, timestamp=0.2)
        self.assertEqual(0, store.snapshot()[1][0].segment_id)

    def test_timestamp_rollback_starts_new_segment(self):
        store = self.make_store()
        store.append(1, 0, 0, quality=2, timestamp=10.0)
        store.append(1, 1, 0, quality=2, timestamp=9.0)
        points = store.snapshot()[1]
        self.assertEqual(points[0].segment_id + 1, points[1].segment_id)

    def test_segment_ids_are_independent_per_node(self):
        store = self.make_store()
        store.append(1, 0, 0, quality=2, timestamp=0.0)
        store.append(2, 0, 0, quality=2, timestamp=0.0)
        store.begin_new_segment(1)
        store.append(1, 1, 0, quality=2, timestamp=0.1)
        store.append(2, 1, 0, quality=2, timestamp=0.1)
        self.assertEqual(1, store.snapshot()[1][-1].segment_id)
        self.assertEqual(0, store.snapshot()[2][-1].segment_id)


if __name__ == "__main__":
    unittest.main()
