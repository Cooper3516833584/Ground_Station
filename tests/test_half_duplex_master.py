import threading
import time
import unittest

from components.fleet_models import *
from components.fleet_protocol import *
from components.half_duplex_master import *


class FakeTransport:
    def __init__(self):
        self.master = None
        self.requests = []
        self.drop = 0
        self.drop_commands = 0
        self.drop_drone_stop = False
        self.drop_drone_polls = False
        self.drop_trace_requests = 0
        self.lock = threading.Lock()

    def write(self, raw):
        request = unpack_frame(raw)
        with self.lock:
            self.requests.append(request)
        if self.drop or (
            self.drop_commands and request.kind == MessageKind.COMMAND
        ):
            if request.kind == MessageKind.COMMAND and self.drop_commands:
                self.drop_commands -= 1
            else:
                self.drop -= 1
            return
        if (
            self.drop_drone_stop
            and request.dst == NodeId.DRONE
            and request.kind == MessageKind.COMMAND
        ):
            return
        if (
            self.drop_drone_polls
            and request.dst == NodeId.DRONE
            and request.kind == MessageKind.POLL
        ):
            return
        if request.kind == MessageKind.POLL:
            payload = encode_report(
                ReportPayload(
                    request.session, request.seq, 0, 1, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0,
                )
            )
            kind = MessageKind.REPORT
        elif request.kind == MessageKind.SURVEY_REQUEST:
            payload = encode_survey_report(
                SurveyReportPayload(
                    request.session, request.seq, 2, 0,
                    0, 0xFF, 0xFF, 0, 0xFF, 0xFF, (0,) * 15,
                )
            )
            kind = MessageKind.SURVEY_REPORT
        elif request.kind == MessageKind.TRACE_REQUEST:
            if self.drop_trace_requests:
                self.drop_trace_requests -= 1
                return
            trace_request = decode_trace_request(request.payload)
            payload = encode_trace_report(
                TraceReportPayload(
                    request.session,
                    request.seq,
                    700,
                    1,
                    trace_request.after_sample_seq + 1,
                    trace_request.after_sample_seq + 1,
                    0,
                    (
                        TraceSample(
                            1000,
                            trace_request.after_sample_seq + 1,
                            0,
                            0,
                            0,
                            4,
                            int(TraceSampleFlags.POSE_VALID),
                        ),
                    ),
                )
            )
            kind = MessageKind.TRACE_REPORT
        else:
            command = decode_command(request.payload)
            payload = encode_ack(
                AckPayload(
                    request.session, request.seq, command.command_id,
                    AckStatus.COMPLETED,
                )
            )
            kind = MessageKind.ACK
        response = Frame(
            VERSION, request.dst, NodeId.GROUND, kind, 0,
            99 + request.dst, 1, payload,
        )
        self.master.feed_bytes(pack_frame(response))


class HalfDuplexMasterTests(unittest.TestCase):
    def make_master(self, transport):
        master = HalfDuplexMaster(
            transport=transport,
            timing=HalfDuplexTiming(0, 0.02, 0, 1, 3, 0.1),
            session=123,
        )
        transport.master = master
        master.start()
        self.addCleanup(master.close)
        return master

    def test_command_retry_reuses_identical_session_seq(self):
        transport = FakeTransport()
        transport.drop_commands = 1
        master = self.make_master(transport)
        result = master.submit_command(
            NodeId.CAR, CommandPayload(CommandId.PING)
        ).result(1)
        self.assertTrue(result.succeeded)
        command_requests = [
            item for item in transport.requests if item.kind == MessageKind.COMMAND
        ]
        self.assertEqual(len(command_requests), 2)
        self.assertEqual(
            (command_requests[0].session, command_requests[0].seq, command_requests[0].payload),
            (command_requests[1].session, command_requests[1].seq, command_requests[1].payload),
        )
        self.assertEqual(master.stats.max_concurrent_writes, 1)

    def test_survey_query_matches_referenced_request(self):
        transport = FakeTransport()
        master = self.make_master(transport)
        result = master.request_survey(NodeId.DRONE).result(1)
        self.assertTrue(result.succeeded)
        survey = decode_survey_report(result.response.payload)
        self.assertEqual(
            (result.request.session, result.request.seq),
            (survey.request_session, survey.request_seq),
        )

    def test_late_drone_reply_does_not_complete_car_transaction(self):
        transport = FakeTransport()
        master = self.make_master(transport)
        request = Frame(
            VERSION, NodeId.GROUND, NodeId.DRONE, MessageKind.POLL,
            0, master.session, 77, encode_poll(PollPayload()),
        )
        late = Frame(
            VERSION, NodeId.DRONE, NodeId.GROUND, MessageKind.REPORT, 0, 9, 1,
            encode_report(ReportPayload(master.session, 77, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
        )
        master.feed_bytes(pack_frame(late))
        result = master.submit_command(
            NodeId.CAR, CommandPayload(CommandId.PING)
        ).result(1)
        self.assertTrue(result.succeeded)
        self.assertGreaterEqual(master.stats.late_frames, 1)

    def test_stop_all_attempts_car_after_drone_timeout(self):
        transport = FakeTransport()
        transport.drop_drone_stop = True
        master = self.make_master(transport)
        result = master.request_stop_all(timeout=1)
        self.assertFalse(result.drone.succeeded)
        self.assertTrue(result.car.succeeded)
        stops = [
            item.dst for item in transport.requests
            if item.kind == MessageKind.COMMAND
            and decode_command(item.payload).command_id == CommandId.TARGETED_STOP
        ]
        self.assertIn(NodeId.DRONE, stops)
        self.assertIn(NodeId.CAR, stops)

    def test_close_stops_background_writes(self):
        transport = FakeTransport()
        master = self.make_master(transport)
        time.sleep(0.03)
        master.close()
        count = len(transport.requests)
        time.sleep(0.04)
        self.assertEqual(len(transport.requests), count)

    def test_offline_node_is_polled_at_recovery_rate(self):
        transport = FakeTransport()
        transport.drop_drone_polls = True
        master = HalfDuplexMaster(
            transport=transport,
            timing=HalfDuplexTiming(0, 0.005, 0, 0, 2, 0.08, 0.005),
            session=123,
        )
        transport.master = master
        master.start()
        self.addCleanup(master.close)
        time.sleep(0.05)
        early = [
            item
            for item in transport.requests
            if item.dst == NodeId.DRONE and item.kind == MessageKind.POLL
        ]
        self.assertEqual(2, len(early))
        time.sleep(0.09)
        later = [
            item
            for item in transport.requests
            if item.dst == NodeId.DRONE and item.kind == MessageKind.POLL
        ]
        self.assertGreaterEqual(len(later), 3)

    def test_trace_request_matches_response_and_has_no_retry(self):
        transport = FakeTransport()
        transport.drop_trace_requests = 1
        master = self.make_master(transport)
        result = master.request_trace(
            NodeId.DRONE, TraceRequestPayload(700, 4, 15)
        ).result(1)
        self.assertFalse(result.succeeded)
        trace_requests = [
            item for item in transport.requests
            if item.kind == MessageKind.TRACE_REQUEST
        ]
        self.assertEqual(1, len(trace_requests))
        self.assertEqual(1, result.attempts)

        result = master.request_trace(
            NodeId.DRONE, TraceRequestPayload(700, 4, 15)
        ).result(1)
        self.assertTrue(result.succeeded)
        report = decode_trace_report(result.response.payload)
        self.assertEqual(
            (result.request.session, result.request.seq),
            (report.request_session, report.request_seq),
        )

    def test_trace_timeout_does_not_mark_regular_poll_timeout(self):
        transport = FakeTransport()
        transport.drop_trace_requests = 1
        timed_out_nodes = []
        master = HalfDuplexMaster(
            transport=transport,
            timing=HalfDuplexTiming(0, 0.01, 0, 0, 3, 1.0, 1.0),
            on_timeout=timed_out_nodes.append,
            session=123,
        )
        transport.master = master
        master.start()
        self.addCleanup(master.close)
        result = master.request_trace(
            NodeId.DRONE, TraceRequestPayload(0, 0, 15)
        ).result(1)
        self.assertFalse(result.succeeded)
        self.assertEqual([], timed_out_nodes)
        self.assertEqual(0, master._missed_polls[int(NodeId.DRONE)])

    def test_due_poll_runs_before_queued_trace(self):
        transport = FakeTransport()
        master = HalfDuplexMaster(
            transport=transport,
            timing=HalfDuplexTiming(0, 0.01, 0, 0, 3, 1.0, 1.0),
            session=123,
        )
        master.request_trace(
            NodeId.DRONE, TraceRequestPayload(0, 0, 4)
        )

        first = master._next_work()
        second = master._next_work()
        third = master._next_work()

        self.assertEqual(MessageKind.POLL, first.kind)
        self.assertEqual(MessageKind.POLL, second.kind)
        self.assertEqual(MessageKind.TRACE_REQUEST, third.kind)

    def test_on_frame_runs_before_future_is_completed(self):
        transport = FakeTransport()
        callback_seen = threading.Event()
        master = HalfDuplexMaster(
            transport=transport,
            timing=HalfDuplexTiming(0, 0.02, 0, 0, 3, 1.0, 1.0),
            on_frame=lambda _frame: callback_seen.set(),
            session=123,
        )
        transport.master = master
        master.start()
        self.addCleanup(master.close)
        result = master.request_trace(
            NodeId.DRONE, TraceRequestPayload(0, 0, 15)
        ).result(1)
        self.assertTrue(result.succeeded)
        self.assertTrue(callback_seen.is_set())

    def test_command_wakes_master_waiting_for_next_poll(self):
        transport = FakeTransport()
        master = HalfDuplexMaster(
            transport=transport,
            timing=HalfDuplexTiming(0, 0.02, 0, 0, 3, 1.0, 1.0),
            session=123,
        )
        transport.master = master
        master.start()
        self.addCleanup(master.close)
        deadline = time.monotonic() + 0.5
        while len(transport.requests) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        started = time.monotonic()
        result = master.submit_command(
            NodeId.CAR, CommandPayload(CommandId.PING)
        ).result(0.3)
        self.assertTrue(result.succeeded)
        self.assertLess(time.monotonic() - started, 0.2)

    def test_online_nodes_are_polled_at_configured_half_second_rate(self):
        transport = FakeTransport()
        master = HalfDuplexMaster(
            transport=transport,
            timing=HalfDuplexTiming(0, 0.02, 0, 0, 3, 5.0, 0.5),
            session=123,
        )
        transport.master = master
        master.start()
        self.addCleanup(master.close)
        time.sleep(0.12)
        early = [item for item in transport.requests if item.kind == MessageKind.POLL]
        self.assertEqual(2, len(early))
        time.sleep(0.48)
        later = [item for item in transport.requests if item.kind == MessageKind.POLL]
        self.assertGreaterEqual(len(later), 4)


if __name__ == "__main__":
    unittest.main()
