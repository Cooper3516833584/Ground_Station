import csv
from pathlib import Path
import sys
import tempfile
import time
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.coordinate_frames import FrameTransform2D  # noqa: E402
from components.fleet_models import (  # noqa: E402
    Frame,
    MessageKind,
    NodeFlags,
    NodeId,
    ReportPayload,
    TraceReportFlags,
    TraceReportPayload,
    TraceSample,
    TraceSampleFlags,
)
from components.fleet_protocol import (  # noqa: E402
    VERSION,
    encode_report,
    encode_trace_report,
)
from components.fleet_store import FleetStore  # noqa: E402
from components.trace_sync import TraceSyncWorker  # noqa: E402
from components.trajectory_store import TrajectoryStore  # noqa: E402


DRONE = int(NodeId.DRONE)
CAR = int(NodeId.CAR)
VALID = int(TraceSampleFlags.POSE_VALID)


def report_frame(
    *, node=DRONE, session=10, seq=1, x=0, y=0, quality=4,
    operation_state=1, node_flags=None
):
    if node_flags is None:
        node_flags = int(NodeFlags.POSE_VALID | NodeFlags.READY)
    return Frame(
        VERSION,
        node,
        NodeId.GROUND,
        MessageKind.REPORT,
        0,
        session,
        seq,
        encode_report(
            ReportPayload(
                1,
                seq,
                node_flags,
                seq * 100,
                x,
                y,
                0,
                0,
                0,
                0,
                0,
                1200,
                operation_state,
                quality,
                0,
                0,
                0,
            )
        ),
    )


def trace_frame(
    samples,
    *,
    node=DRONE,
    frame_session=10,
    trace_session=20,
    first=1,
    latest=None,
    flags=0,
    seq=1,
):
    samples = tuple(samples)
    if latest is None:
        latest = first + len(samples) - 1 if samples else 0
    return Frame(
        VERSION,
        node,
        NodeId.GROUND,
        MessageKind.TRACE_REPORT,
        0,
        frame_session,
        seq,
        encode_trace_report(
            TraceReportPayload(
                1,
                seq,
                trace_session,
                first if samples else 0,
                first if samples else 0,
                latest,
                flags,
                samples,
            )
        ),
    )


def sample(uptime, x, y=0, *, quality=4, flags=VALID):
    return TraceSample(uptime, x, y, 0, 0, quality, flags)


class FleetStoreTraceTests(unittest.TestCase):
    def setUp(self):
        self.store = FleetStore()
        self.store.set_frame_transform(DRONE, FrameTransform2D(100, 200, 90))
        self.store.set_frame_transform(CAR, FrameTransform2D(0, 0, 0))

    def points(self, node=DRONE):
        return self.store.trajectories.snapshot()[node]

    def test_first_nonempty_trace_replaces_report_fallback_and_maps_time(self):
        self.store.handle_frame(report_frame(x=5))
        self.assertEqual(1, len(self.points()))
        self.store.handle_frame(trace_frame(
            (sample(1000, 0), sample(1100, 10), sample(1200, 20)),
            flags=int(TraceReportFlags.CURSOR_RESET),
        ))
        points = self.points()
        self.assertEqual(3, len(points))
        self.assertTrue(self.store.trace_cursor(DRONE).active)
        self.assertEqual([1, 2, 3], [point.sample_seq for point in points])
        self.assertEqual(["trace"] * 3, [point.source for point in points])
        self.assertAlmostEqual(0.1, points[1].timestamp - points[0].timestamp, places=4)
        self.assertAlmostEqual(0.1, points[2].timestamp - points[1].timestamp, places=4)
        self.assertAlmostEqual((100.0, 200.0), (points[0].x_cm, points[0].y_cm))
        self.assertAlmostEqual((100.0, 210.0), (points[1].x_cm, points[1].y_cm))
        self.assertAlmostEqual(90.0, points[0].heading_deg)

    def test_duplicate_and_partial_overlap_only_append_new_samples(self):
        first = trace_frame((sample(1000, 0), sample(1100, 10)), first=1)
        self.store.handle_frame(first)
        self.store.handle_frame(first)
        self.store.handle_frame(trace_frame(
            (sample(1100, 10), sample(1200, 20)), first=2, latest=3
        ))
        self.assertEqual([1, 2, 3], [point.sample_seq for point in self.points()])

    def test_sequence_gap_overrun_and_trace_session_change_break_segments(self):
        self.store.handle_frame(trace_frame((sample(1000, 0),), first=1))
        self.store.handle_frame(trace_frame((sample(1200, 20),), first=3, latest=3))
        self.assertEqual(1, self.store.trace_cursor(DRONE).sequence_gaps)
        segment_after_gap = self.points()[-1].segment_id
        self.store.handle_frame(trace_frame(
            (sample(1300, 30),),
            first=4,
            latest=4,
            flags=int(TraceReportFlags.BUFFER_OVERRUN),
        ))
        self.assertGreater(self.points()[-1].segment_id, segment_after_gap)
        self.assertEqual(1, self.store.trace_cursor(DRONE).buffer_overruns)
        segment_after_overrun = self.points()[-1].segment_id
        self.store.handle_frame(trace_frame(
            (sample(1400, 40),), trace_session=21, first=1, latest=1
        ))
        self.assertGreater(self.points()[-1].segment_id, segment_after_overrun)

    def test_invalid_pose_advances_cursor_and_next_valid_starts_new_segment(self):
        self.store.handle_frame(trace_frame((sample(1000, 0),), first=1))
        self.store.handle_frame(trace_frame(
            (sample(1100, 10, flags=0), sample(1200, 20)),
            first=2,
            latest=3,
        ))
        self.assertEqual(3, self.store.trace_cursor(DRONE).last_sample_seq)
        self.assertEqual([1, 3], [point.sample_seq for point in self.points()])
        self.assertGreater(self.points()[-1].segment_id, self.points()[0].segment_id)

    def test_uptime_reversal_after_zero_starts_new_segment_and_records_error(self):
        self.store.handle_frame(trace_frame((sample(0, 0),), first=1))
        first_segment = self.points()[-1].segment_id
        self.store.handle_frame(trace_frame((sample(0, 10),), first=2, latest=2))
        self.assertGreater(self.points()[-1].segment_id, first_segment)
        self.assertIn(
            "trace uptime moved backwards",
            self.store.snapshot().drone.errors,
        )

    def test_single_trace_keeps_report_fallback_active(self):
        self.store.handle_frame(report_frame(x=7, y=8, quality=3))
        self.store.handle_frame(trace_frame((sample(1000, 0),), first=1))
        before = len(self.points())
        self.store.handle_frame(report_frame(seq=2, x=70, y=80, quality=2))
        snapshot = self.store.snapshot().drone
        self.assertEqual((70, 80, 2), (snapshot.x_cm, snapshot.y_cm, snapshot.pose_quality))
        self.assertEqual(before + 1, len(self.points()))
        self.assertFalse(self.store.trace_cursor(DRONE).active)

    def test_new_fleet_session_resets_trace_and_restores_report_fallback(self):
        self.store.handle_frame(report_frame(session=10))
        self.store.handle_frame(trace_frame(
            (sample(1000, 0), sample(1100, 10)), frame_session=10
        ))
        self.assertTrue(self.store.trace_cursor(DRONE).active)
        self.store.handle_frame(report_frame(session=11, seq=2, x=50))
        self.assertFalse(self.store.trace_cursor(DRONE).active)
        self.assertEqual(1, len(self.points()))
        self.assertEqual("report", self.points()[0].source)

    def test_empty_dispatcher_trace_session_preserves_completed_trace(self):
        self.store.handle_frame(report_frame(session=10, operation_state=5))
        self.store.handle_frame(trace_frame(
            (sample(1000, 0), sample(1100, 10)),
            frame_session=10,
        ))

        self.store.handle_frame(trace_frame(
            (),
            frame_session=11,
            trace_session=21,
            seq=2,
        ))

        self.assertEqual(2, len(self.points()))
        self.assertFalse(self.store.trace_cursor(DRONE).active)

    def test_next_task_nonempty_trace_clears_completed_trace(self):
        self.store.handle_frame(report_frame(session=10, operation_state=5))
        self.store.handle_frame(trace_frame(
            (sample(1000, 0), sample(1100, 10)),
            frame_session=10,
        ))
        self.store.handle_frame(trace_frame(
            (),
            frame_session=11,
            trace_session=21,
            seq=2,
        ))

        self.store.handle_frame(trace_frame(
            (sample(2000, 20),),
            frame_session=12,
            trace_session=22,
            seq=3,
        ))

        points = self.points()
        self.assertEqual(1, len(points))
        self.assertEqual((1, "trace"), (points[0].sample_seq, points[0].source))

    def test_car_idle_session_preserves_completed_trace(self):
        self.store.handle_frame(report_frame(
            node=CAR, session=10, operation_state=5
        ))
        self.store.handle_frame(trace_frame(
            (sample(1000, 0), sample(1100, 10)),
            node=CAR,
            frame_session=10,
        ))

        self.store.handle_frame(report_frame(
            node=CAR,
            session=11,
            seq=2,
            quality=0,
            operation_state=0,
            node_flags=int(NodeFlags.READY),
        ))

        points = self.points(CAR)
        self.assertEqual(2, len(points))
        self.assertTrue(all(point.source == "trace" for point in points))
        self.assertFalse(self.store.trace_cursor(CAR).active)

    def test_car_next_requested_task_clears_completed_trace(self):
        self.store.handle_frame(report_frame(
            node=CAR, session=10, operation_state=5
        ))
        self.store.handle_frame(trace_frame(
            (sample(1000, 0), sample(1100, 10)),
            node=CAR,
            frame_session=10,
        ))
        self.store.handle_frame(report_frame(
            node=CAR,
            session=11,
            seq=2,
            quality=0,
            operation_state=0,
            node_flags=int(NodeFlags.READY),
        ))

        self.store.handle_frame(report_frame(
            node=CAR,
            session=11,
            seq=3,
            quality=0,
            operation_state=13,
            node_flags=int(NodeFlags.READY),
        ))
        self.assertEqual(0, len(self.points(CAR)))

        self.store.handle_frame(report_frame(
            node=CAR,
            session=12,
            seq=4,
            x=50,
            operation_state=3,
        ))
        self.store.handle_frame(report_frame(
            node=CAR,
            session=12,
            seq=5,
            x=60,
            operation_state=4,
        ))
        points = self.points(CAR)
        self.assertEqual(2, len(points))
        self.assertEqual(
            [(50, "report"), (60, "report")],
            [(point.x_cm, point.source) for point in points],
        )

    def test_single_car_trace_keeps_report_fallback_active(self):
        self.store.handle_frame(report_frame(
            node=CAR, x=7, y=8, quality=3, operation_state=4
        ))
        self.store.handle_frame(trace_frame(
            (sample(1000, 0),), node=CAR, first=1
        ))
        self.store.handle_frame(report_frame(
            node=CAR, seq=2, x=70, y=80, quality=2, operation_state=4
        ))

        points = self.points(CAR)
        self.assertEqual(2, len(points))
        self.assertTrue(all(point.source == "report" for point in points))
        self.assertFalse(self.store.trace_cursor(CAR).active)

    def test_two_valid_car_trace_points_replace_report_fallback(self):
        self.store.handle_frame(report_frame(
            node=CAR, x=7, y=8, operation_state=4
        ))
        self.store.handle_frame(trace_frame(
            (sample(1000, 0), sample(1100, 10)), node=CAR
        ))

        points = self.points(CAR)
        self.assertEqual(2, len(points))
        self.assertTrue(all(point.source == "trace" for point in points))
        self.assertTrue(self.store.trace_cursor(CAR).active)

    def test_car_report_fallback_resumes_after_trace_failure(self):
        self.store.handle_frame(report_frame(
            node=CAR, operation_state=4
        ))
        self.store.handle_frame(trace_frame(
            (sample(1000, 0), sample(1100, 10)), node=CAR
        ))
        self.store.note_trace_failure(CAR)
        self.store.handle_frame(report_frame(
            node=CAR, seq=2, x=20, operation_state=4
        ))

        self.assertEqual(
            ["trace", "trace", "report"],
            [point.source for point in self.points(CAR)],
        )

    def test_node_cursors_are_independent_and_empty_trace_does_not_activate(self):
        self.store.handle_frame(trace_frame((), node=DRONE, trace_session=30))
        self.assertFalse(self.store.trace_cursor(DRONE).active)
        self.store.handle_frame(trace_frame((sample(1000, 1),), node=CAR, trace_session=40))
        self.assertEqual(0, self.store.trace_cursor(DRONE).last_sample_seq)
        self.assertEqual(1, self.store.trace_cursor(CAR).last_sample_seq)

    def test_csv_contains_trace_metadata(self):
        self.store.handle_frame(trace_frame((sample(1000, 0),), first=1))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.csv"
            self.store.trajectories.export_csv(str(path))
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertIn("sample_seq", rows[0])
        self.assertIn("device_uptime_ms", rows[0])
        self.assertIn("source", rows[0])
        self.assertEqual(("1", "1000", "trace"), (
            rows[0]["sample_seq"], rows[0]["device_uptime_ms"], rows[0]["source"]
        ))


class _Result:
    def __init__(self, succeeded=True):
        self.succeeded = succeeded


class _Future:
    def __init__(self, result):
        self._result = result

    def result(self, _timeout):
        return self._result


class _Master:
    def __init__(self, store, more_pending=False, succeed=True):
        self.store = store
        self.more_pending = more_pending
        self.succeed = succeed
        self.requests = []

    def request_trace(self, node_id, request):
        self.requests.append((node_id, request))
        if self.succeed:
            self.store.more_pending = self.more_pending and len(self.requests) < 3
        return _Future(_Result(self.succeed))


class _Store:
    def __init__(self):
        self.more_pending = False
        self.failures = 0

    def node_online(self, _node_id):
        return True

    def trace_cursor(self, _node_id):
        from components.trace_sync import TraceCursorSnapshot
        return TraceCursorSnapshot(
            7,
            8,
            self.more_pending,
            consecutive_failures=self.failures,
        )

    def note_trace_failure(self, _node_id):
        self.failures += 1


class TraceSyncWorkerTests(unittest.TestCase):
    def test_catchup_is_bounded_and_uses_current_cursor(self):
        store = _Store()
        master = _Master(store, more_pending=True)
        worker = TraceSyncWorker(
            master=master,
            store=store,
            node_ids=(DRONE, CAR),
            max_catchup_batches=2,
        )
        worker._request_node(DRONE)
        self.assertEqual(3, len(master.requests))
        self.assertEqual((7, 8, 15), (
            master.requests[0][1].known_trace_session,
            master.requests[0][1].after_sample_seq,
            master.requests[0][1].max_samples,
        ))

    def test_failure_does_not_advance_cursor_and_is_counted(self):
        store = _Store()
        master = _Master(store, succeed=False)
        worker = TraceSyncWorker(master=master, store=store, node_ids=(DRONE,))
        worker._request_node(DRONE)
        self.assertEqual(1, store.failures)
        self.assertEqual(8, master.requests[0][1].after_sample_seq)

    def test_failure_backoff_is_bounded(self):
        store = _Store()
        worker = TraceSyncWorker(
            master=_Master(store),
            store=store,
            node_ids=(DRONE,),
            request_interval_s=1.0,
        )

        self.assertEqual(2.0, worker._failure_backoff_s(1))
        self.assertEqual(4.0, worker._failure_backoff_s(2))
        self.assertEqual(8.0, worker._failure_backoff_s(3))
        self.assertEqual(8.0, worker._failure_backoff_s(20))

    def test_start_close_are_idempotent(self):
        store = _Store()
        master = _Master(store)
        worker = TraceSyncWorker(
            master=master,
            store=store,
            node_ids=(DRONE, CAR),
            request_interval_s=0.02,
            transaction_wait_timeout_s=0.1,
        )
        worker.start()
        worker.start()
        time.sleep(0.04)
        worker.close()
        worker.close()
        self.assertFalse(worker.running)
        nodes = [item[0] for item in master.requests]
        self.assertIn(DRONE, nodes)
        self.assertIn(CAR, nodes)


if __name__ == "__main__":
    unittest.main()
