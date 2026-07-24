"""Single-writer FleetBus V1 half-duplex master."""

from dataclasses import dataclass
import queue
import threading
import time
from typing import Callable, Optional, Tuple

from .fleet_models import (
    AckPayload,
    CommandId,
    CommandPayload,
    Frame,
    MessageKind,
    NodeId,
    PollPayload,
)
from .fleet_protocol import (
    FrameParser,
    SequenceCounter,
    VERSION,
    decode_ack,
    decode_report,
    encode_command,
    encode_poll,
    new_session,
    pack_frame,
)


@dataclass(frozen=True)
class HalfDuplexTiming:
    node_turnaround_s: float = 0.20
    response_timeout_s: float = 0.50
    inter_slot_guard_s: float = 0.10
    command_retries: int = 3
    offline_after_missed_polls: int = 3
    offline_poll_interval_s: float = 5.0

    def __post_init__(self) -> None:
        if min(
            self.node_turnaround_s,
            self.response_timeout_s,
            self.inter_slot_guard_s,
            self.offline_poll_interval_s,
        ) < 0:
            raise ValueError("half-duplex timing values must not be negative")
        if self.command_retries < 0 or self.offline_after_missed_polls <= 0:
            raise ValueError("retry/offline counts are invalid")


@dataclass
class MasterStats:
    transactions: int = 0
    responses: int = 0
    timeouts: int = 0
    late_frames: int = 0
    unexpected_frames: int = 0
    write_failures: int = 0
    max_concurrent_writes: int = 0


@dataclass(frozen=True)
class TransactionResult:
    node_id: int
    request: Frame
    response: Optional[Frame]
    attempts: int
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.response is not None


@dataclass(frozen=True)
class StopAllResult:
    drone: TransactionResult
    car: TransactionResult


class ResultFuture:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._result = None  # type: Optional[TransactionResult]

    def set_result(self, result: TransactionResult) -> None:
        self._result = result
        self._event.set()

    def result(self, timeout: Optional[float] = None) -> TransactionResult:
        if not self._event.wait(timeout):
            raise TimeoutError("FleetBus transaction did not finish")
        assert self._result is not None
        return self._result


@dataclass(frozen=True)
class _Work:
    priority: int
    order: int
    node_id: int
    kind: int
    payload: bytes
    retries: int
    future: ResultFuture


class HalfDuplexMaster:
    PRIORITY_STOP = 0
    PRIORITY_COMMAND = 20
    PRIORITY_QUERY = 40

    def __init__(
        self,
        *,
        transport,
        timing: HalfDuplexTiming = HalfDuplexTiming(),
        on_frame: Optional[Callable[[Frame], None]] = None,
        on_timeout: Optional[Callable[[int], None]] = None,
        session: Optional[int] = None,
    ) -> None:
        self.transport = transport
        self.timing = timing
        self.on_frame = on_frame
        self.on_timeout = on_timeout
        self.session = new_session() if session is None else session
        self.stats = MasterStats()
        self._seq = SequenceCounter()
        self._parser = FrameParser(local_node=NodeId.GROUND)
        self._inbound = queue.Queue()
        self._work = queue.PriorityQueue()
        self._stop = threading.Event()
        self._thread = None  # type: Optional[threading.Thread]
        self._order = 0
        self._order_lock = threading.Lock()
        self._poll_index = 0
        self._write_lock = threading.Lock()
        self._write_depth = 0
        self._closed = False
        self._missed_polls = {
            int(NodeId.DRONE): 0,
            int(NodeId.CAR): 0,
        }
        self._last_poll_at = {
            int(NodeId.DRONE): 0.0,
            int(NodeId.CAR): 0.0,
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._closed = False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="fleetbus-master", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._closed = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def feed_bytes(self, data: bytes) -> None:
        if self._closed:
            return
        for frame in self._parser.feed(data):
            self._inbound.put(frame)

    def submit_command(
        self, node_id: int, command: CommandPayload
    ) -> ResultFuture:
        if node_id == NodeId.BROADCAST:
            raise ValueError("broadcast control commands are forbidden")
        priority = (
            self.PRIORITY_STOP
            if command.command_id == CommandId.TARGETED_STOP
            else self.PRIORITY_COMMAND
        )
        return self._submit(
            priority,
            node_id,
            MessageKind.COMMAND,
            encode_command(command),
            self.timing.command_retries,
        )

    def request_map(self, node_id: int) -> ResultFuture:
        return self._submit(
            self.PRIORITY_QUERY, node_id, MessageKind.MAP_REQUEST, b"", 0
        )

    def request_path(self, node_id: int) -> ResultFuture:
        return self._submit(
            self.PRIORITY_QUERY, node_id, MessageKind.PATH_REQUEST, b"", 0
        )

    def request_stop_all(self, timeout: Optional[float] = None) -> StopAllResult:
        command = CommandPayload(CommandId.TARGETED_STOP)
        drone = self.submit_command(NodeId.DRONE, command).result(timeout)
        car = self.submit_command(NodeId.CAR, command).result(timeout)
        return StopAllResult(drone, car)

    def _submit(
        self, priority: int, node_id: int, kind: int, payload: bytes, retries: int
    ) -> ResultFuture:
        if self._closed:
            raise RuntimeError("FleetBus master is closed")
        future = ResultFuture()
        with self._order_lock:
            self._order += 1
            order = self._order
        work = _Work(priority, order, int(node_id), int(kind), bytes(payload), retries, future)
        self._work.put((priority, order, work))
        return future

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                _, _, work = self._work.get_nowait()
            except queue.Empty:
                node = self._next_poll_node()
                if node is None:
                    self._stop.wait(min(0.05, self.timing.offline_poll_interval_s))
                    continue
                future = ResultFuture()
                work = _Work(
                    100,
                    0,
                    int(node),
                    int(MessageKind.POLL),
                    encode_poll(PollPayload()),
                    0,
                    future,
                )
            result = self._execute(work)
            work.future.set_result(result)

    def _next_poll_node(self) -> Optional[NodeId]:
        now = time.monotonic()
        for _ in range(2):
            node = (NodeId.DRONE, NodeId.CAR)[self._poll_index % 2]
            self._poll_index += 1
            node_id = int(node)
            offline = (
                self._missed_polls[node_id]
                >= self.timing.offline_after_missed_polls
            )
            if (
                not offline
                or now - self._last_poll_at[node_id]
                >= self.timing.offline_poll_interval_s
            ):
                self._last_poll_at[node_id] = now
                return node
        return None

    def _execute(self, work: _Work) -> TransactionResult:
        request = Frame(
            VERSION,
            NodeId.GROUND,
            work.node_id,
            work.kind,
            0,
            self.session,
            self._seq.next(),
            work.payload,
        )
        raw = pack_frame(request)
        attempts = 0
        response = None
        error = ""
        for attempts in range(1, work.retries + 2):
            if self._stop.is_set():
                error = "master closing"
                break
            self.stats.transactions += 1
            try:
                self._write(raw)
            except RuntimeError as exc:
                self.stats.write_failures += 1
                error = str(exc)
            else:
                response = self._wait_for_response(request)
                if response is not None:
                    self.stats.responses += 1
                    self._missed_polls[work.node_id] = 0
                    break
                self.stats.timeouts += 1
                if work.kind == int(MessageKind.POLL):
                    self._missed_polls[work.node_id] += 1
                error = "response timeout"
                if self.on_timeout is not None:
                    self.on_timeout(work.node_id)
            if self._stop.wait(self.timing.inter_slot_guard_s):
                break
        if response is not None and self.on_frame is not None:
            self.on_frame(response)
        if not self._stop.is_set():
            self._stop.wait(self.timing.inter_slot_guard_s)
        return TransactionResult(work.node_id, request, response, attempts, error)

    def _write(self, raw: bytes) -> None:
        with self._write_lock:
            self._write_depth += 1
            self.stats.max_concurrent_writes = max(
                self.stats.max_concurrent_writes, self._write_depth
            )
            try:
                self.transport.write(raw)
            finally:
                self._write_depth -= 1

    def _wait_for_response(self, request: Frame) -> Optional[Frame]:
        deadline = time.monotonic() + self.timing.response_timeout_s
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                frame = self._inbound.get(timeout=remaining)
            except queue.Empty:
                return None
            if self._matches(request, frame):
                return frame
            if self._references_older_request(frame):
                self.stats.late_frames += 1
            else:
                self.stats.unexpected_frames += 1
        return None

    @staticmethod
    def _request_reference(frame: Frame) -> Optional[Tuple[int, int]]:
        try:
            if frame.kind == MessageKind.ACK:
                value = decode_ack(frame.payload)
                return value.request_session, value.request_seq
            if frame.kind == MessageKind.REPORT:
                value = decode_report(frame.payload)
                return value.request_session, value.request_seq
            if frame.kind in (MessageKind.MAP_REPORT, MessageKind.PATH_REPORT):
                if len(frame.payload) >= 6:
                    return (
                        int.from_bytes(frame.payload[:4], "little"),
                        int.from_bytes(frame.payload[4:6], "little"),
                    )
        except ValueError:
            return None
        return None

    def _matches(self, request: Frame, response: Frame) -> bool:
        if response.src != request.dst or response.dst != NodeId.GROUND:
            return False
        allowed = {
            MessageKind.POLL: (MessageKind.REPORT,),
            MessageKind.COMMAND: (MessageKind.ACK,),
            MessageKind.MAP_REQUEST: (MessageKind.MAP_REPORT,),
            MessageKind.PATH_REQUEST: (MessageKind.PATH_REPORT,),
        }.get(MessageKind(request.kind), ())
        return response.kind in allowed and self._request_reference(response) == (
            request.session,
            request.seq,
        )

    def _references_older_request(self, frame: Frame) -> bool:
        reference = self._request_reference(frame)
        return reference is not None and reference[0] == self.session
