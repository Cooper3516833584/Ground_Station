"""Low-rate FleetBus trace download scheduling and cursor snapshots."""

from dataclasses import dataclass
import threading
import time
from typing import Iterable, Optional

from .fleet_models import TraceRequestPayload


@dataclass(frozen=True)
class TraceCursorSnapshot:
    trace_session: int = 0
    last_sample_seq: int = 0
    more_pending: bool = False
    active: bool = False
    consecutive_failures: int = 0
    buffer_overruns: int = 0
    sequence_gaps: int = 0


@dataclass
class TraceClockState:
    trace_session: int
    anchor_uptime_ms: int
    anchor_wall_time: float
    last_uptime_ms: int


class TraceSyncWorker:
    def __init__(
        self,
        *,
        master,
        store,
        node_ids: Iterable[int],
        request_interval_s: float = 1.0,
        max_samples: int = 15,
        max_catchup_batches: int = 2,
        transaction_wait_timeout_s: float = 3.0,
        monotonic=time.monotonic,
    ) -> None:
        self._master = master
        self._store = store
        self._node_ids = tuple(int(node_id) for node_id in node_ids)
        if not self._node_ids:
            raise ValueError("node_ids must not be empty")
        if request_interval_s <= 0:
            raise ValueError("request_interval_s must be positive")
        if not 1 <= max_samples <= 15:
            raise ValueError("max_samples must be in 1..15")
        if max_catchup_batches < 0:
            raise ValueError("max_catchup_batches must not be negative")
        if transaction_wait_timeout_s <= 0:
            raise ValueError("transaction_wait_timeout_s must be positive")
        self._request_interval_s = float(request_interval_s)
        self._max_samples = int(max_samples)
        self._max_catchup_batches = int(max_catchup_batches)
        self._transaction_wait_timeout_s = float(transaction_wait_timeout_s)
        self._monotonic = monotonic
        self._stop = threading.Event()
        self._thread = None  # type: Optional[threading.Thread]
        self._lifecycle_lock = threading.Lock()

    @property
    def running(self) -> bool:
        with self._lifecycle_lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="fleet-trace-sync",
                daemon=True,
            )
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with self._lifecycle_lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._transaction_wait_timeout_s + 1.0)
        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def _run(self) -> None:
        now = self._monotonic()
        spacing = self._request_interval_s / float(len(self._node_ids))
        next_due = {
            node_id: now + index * spacing
            for index, node_id in enumerate(self._node_ids)
        }
        while not self._stop.is_set():
            node_id = min(self._node_ids, key=lambda item: next_due[item])
            wait_s = next_due[node_id] - self._monotonic()
            if wait_s > 0 and self._stop.wait(wait_s):
                return
            due = next_due[node_id] + self._request_interval_s
            now = self._monotonic()
            next_due[node_id] = due if due > now else now + self._request_interval_s
            if not self._store.node_online(node_id):
                continue
            self._request_node(node_id)

    def _request_node(self, node_id: int) -> None:
        for batch_index in range(self._max_catchup_batches + 1):
            if self._stop.is_set():
                return
            cursor = self._store.trace_cursor(node_id)
            request = TraceRequestPayload(
                known_trace_session=cursor.trace_session,
                after_sample_seq=cursor.last_sample_seq,
                max_samples=self._max_samples,
            )
            try:
                result = self._master.request_trace(node_id, request).result(
                    self._transaction_wait_timeout_s
                )
            except (RuntimeError, TimeoutError):
                self._store.note_trace_failure(node_id)
                return
            if not result.succeeded:
                self._store.note_trace_failure(node_id)
                return
            updated = self._store.trace_cursor(node_id)
            if not updated.more_pending or batch_index >= self._max_catchup_batches:
                return
