import threading
import time
import unittest
from collections import deque

from components.coordinate_frames import FrameTransform2D
from components.fleet_models import (
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
from components.fleet_protocol import (
    SequenceCounter,
    VERSION,
    decode_trace_request,
    encode_report,
    encode_trace_report,
    pack_frame,
    unpack_frame,
)
from components.fleet_store import FleetStore
from components.half_duplex_master import HalfDuplexMaster, HalfDuplexTiming
from components.trace_sync import TraceSyncWorker


CAR = int(NodeId.CAR)
VALID = int(TraceSampleFlags.POSE_VALID)


class _InMemoryTraceDevice:
    """Protocol-level fake device connected directly to the master writer."""

    def __init__(self, capacity=600):
        self.master = None
        self.samples = deque(maxlen=capacity)
        self.trace_session = 700
        self.frame_session = 900
        self.frame_seq = SequenceCounter()
        self.next_sample_seq = 1
        self.drop_next_trace_report = False
        self.trace_request_cursors = []
        self.poll_report_count = 0
        self._lock = threading.Lock()

    def record(self, uptime_ms, x_cm):
        with self._lock:
            self.samples.append((
                self.next_sample_seq,
                TraceSample(uptime_ms, x_cm, 0, 0, 0, 4, VALID),
            ))
            self.next_sample_seq += 1

    def write(self, raw):
        request = unpack_frame(raw)
        if request.kind == MessageKind.POLL:
            self._reply_report(request)
        elif request.kind == MessageKind.TRACE_REQUEST:
            self._reply_trace(request)

    def _send(self, request, kind, payload):
        response = Frame(
            VERSION,
            request.dst,
            NodeId.GROUND,
            kind,
            0,
            self.frame_session,
            self.frame_seq.next(),
            payload,
        )
        self.master.feed_bytes(pack_frame(response))

    def _reply_report(self, request):
        with self._lock:
            latest = self.samples[-1][1] if self.samples else TraceSample(
                0, 0, 0, 0, 0, 4, VALID
            )
        self.poll_report_count += 1
        self._send(
            request,
            MessageKind.REPORT,
            encode_report(ReportPayload(
                request.session,
                request.seq,
                int(NodeFlags.POSE_VALID | NodeFlags.READY),
                latest.uptime_ms,
                latest.x_cm,
                latest.y_cm,
                latest.z_cm,
                latest.heading_cdeg,
                0,
                0,
                0,
                1200,
                0,
                latest.quality,
                0,
                0,
                0,
            )),
        )

    def _reply_trace(self, request):
        trace_request = decode_trace_request(request.payload)
        self.trace_request_cursors.append(trace_request.after_sample_seq)
        with self._lock:
            snapshot = tuple(self.samples)
        if not snapshot:
            report = TraceReportPayload(
                request.session, request.seq, self.trace_session,
                0, 0, 0, 0, (),
            )
        else:
            oldest = snapshot[0][0]
            latest = snapshot[-1][0]
            flags = TraceReportFlags.NONE
            if trace_request.known_trace_session != self.trace_session:
                start = 0
                flags |= TraceReportFlags.CURSOR_RESET
            elif trace_request.after_sample_seq + 1 < oldest:
                start = 0
                flags |= TraceReportFlags.BUFFER_OVERRUN
            elif trace_request.after_sample_seq >= latest:
                start = len(snapshot)
            else:
                start = trace_request.after_sample_seq + 1 - oldest
            selected = snapshot[start:start + trace_request.max_samples]
            if selected and selected[-1][0] < latest:
                flags |= TraceReportFlags.MORE_PENDING
            report = TraceReportPayload(
                request.session,
                request.seq,
                self.trace_session,
                oldest,
                selected[0][0] if selected else 0,
                latest,
                int(flags),
                tuple(item[1] for item in selected),
            )
        if self.drop_next_trace_report:
            self.drop_next_trace_report = False
            return
        self._send(
            request, MessageKind.TRACE_REPORT, encode_trace_report(report)
        )


class TraceIntegrationTests(unittest.TestCase):
    def make_stack(self, device, online_poll_interval=0.05):
        store = FleetStore()
        store.set_frame_transform(CAR, FrameTransform2D(0, 0, 0))
        master = HalfDuplexMaster(
            transport=device,
            timing=HalfDuplexTiming(
                node_turnaround_s=0,
                response_timeout_s=0.01,
                inter_slot_guard_s=0,
                command_retries=0,
                offline_after_missed_polls=3,
                offline_poll_interval_s=1.0,
                online_poll_interval_s=online_poll_interval,
            ),
            on_frame=store.handle_frame,
            on_timeout=store.mark_timeout,
            session=123,
        )
        device.master = master
        master.start()
        self.addCleanup(master.close)
        return store, master

    def test_twenty_local_samples_survive_dropped_first_batch(self):
        device = _InMemoryTraceDevice()
        for index in range(20):
            device.record(index * 100, index * 2)
        store, master = self.make_stack(device)
        deadline = time.monotonic() + 0.5
        while not store.node_online(CAR) and time.monotonic() < deadline:
            time.sleep(0.005)

        device.drop_next_trace_report = True
        worker = TraceSyncWorker(
            master=master,
            store=store,
            node_ids=(CAR,),
            max_samples=15,
            max_catchup_batches=2,
            transaction_wait_timeout_s=0.2,
        )
        worker._request_node(CAR)
        self.assertEqual(0, store.trace_cursor(CAR).last_sample_seq)
        worker._request_node(CAR)

        points = store.trajectories.snapshot()[CAR]
        self.assertEqual(list(range(1, 21)), [point.sample_seq for point in points])
        self.assertEqual(20, len({point.sample_seq for point in points}))
        self.assertEqual([0, 0, 15], device.trace_request_cursors)
        self.assertGreaterEqual(device.poll_report_count, 1)
        self.assertGreater(len(points), device.poll_report_count)

    def test_buffer_overrun_resumes_at_oldest_and_breaks_segment(self):
        device = _InMemoryTraceDevice(capacity=5)
        device.record(100, 1)
        store, master = self.make_stack(device, online_poll_interval=10.0)
        deadline = time.monotonic() + 0.5
        while not store.node_online(CAR) and time.monotonic() < deadline:
            time.sleep(0.005)

        worker = TraceSyncWorker(
            master=master,
            store=store,
            node_ids=(CAR,),
            max_samples=15,
            max_catchup_batches=0,
            transaction_wait_timeout_s=0.2,
        )
        worker._request_node(CAR)
        first_segment = store.trajectories.snapshot()[CAR][-1].segment_id
        for index in range(2, 11):
            device.record(index * 100, index)
        worker._request_node(CAR)

        points = store.trajectories.snapshot()[CAR]
        self.assertEqual([1, 6, 7, 8, 9, 10], [point.sample_seq for point in points])
        self.assertGreater(points[1].segment_id, first_segment)
        cursor = store.trace_cursor(CAR)
        self.assertEqual(1, cursor.buffer_overruns)
        self.assertEqual(10, cursor.last_sample_seq)


if __name__ == "__main__":
    unittest.main()
