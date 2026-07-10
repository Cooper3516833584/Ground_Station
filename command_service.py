from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import threading
import time
from typing import Callable, Protocol

from models import AckStatus, Command, CommandAck, CommandId, MessageType, RejectReason
from protocol import Frame, pack_frame


class ByteWriter(Protocol):
    def write(self, data: bytes) -> None:
        ...


@dataclass
class PendingCommand:
    command: Command
    seq: int
    frame_bytes: bytes
    sent_at: float
    retransmits: int = 0
    ack: CommandAck | None = None
    protocol_acknowledged: bool = False
    done: bool = False
    failed_reason: str = ""
    terminal_event: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False
    )


class CommandService:
    def __init__(
        self,
        *,
        writer: ByteWriter,
        key: bytes,
        session: int,
        now: Callable[[], float] = time.monotonic,
        timeout_seconds: float = 0.8,
        max_retries: int = 3,
    ):
        self._writer = writer
        self._key = key
        self._session = session
        self._now = now
        self._timeout = timeout_seconds
        self._max_retransmits = max_retries
        self._next_seq = 1
        self._pending: OrderedDict[int, PendingCommand] = OrderedDict()
        self._latest_seq: int | None = None
        self._lock = threading.Lock()

    @property
    def pending(self) -> PendingCommand | None:
        with self._lock:
            if self._latest_seq is None:
                return None
            return self._pending.get(self._latest_seq)

    def pending_for_seq(self, seq: int) -> PendingCommand | None:
        with self._lock:
            return self._pending.get(seq)

    def wait_for_terminal(
        self, seq: int, timeout: float | None = None
    ) -> PendingCommand | None:
        with self._lock:
            pending = self._pending.get(seq)
            if pending is None:
                return None
            terminal_event = pending.terminal_event
        terminal_event.wait(timeout)
        with self._lock:
            return self._pending.get(seq)

    def send(self, command: Command) -> int:
        with self._lock:
            for item in self._pending.values():
                if item.ack is not None and self._is_terminal(item.ack.status):
                    item.done = True
                    item.terminal_event.set()
            active = [item for item in self._pending.values() if not item.done]
            if active and command.command_id != CommandId.STOP_MISSION:
                raise RuntimeError("another command is pending")
            seq = self._alloc_seq()
            frame_bytes = pack_frame(
                MessageType.COMMAND,
                command.to_payload(),
                session=self._session,
                seq=seq,
                key=self._key,
            )
            pending = PendingCommand(command, seq, frame_bytes, self._now())
            self._pending[seq] = pending
            self._latest_seq = seq
            while len(self._pending) > 64:
                self._pending.popitem(last=False)
        try:
            self._writer.write(frame_bytes)
        except Exception:
            with self._lock:
                self._pending.pop(seq, None)
            raise
        return seq

    def on_ack(self, frame: Frame) -> CommandAck | None:
        if frame.msg_type not in (MessageType.COMMAND_ACK, MessageType.COMMAND_RESULT):
            return None
        ack = CommandAck.from_payload(frame.payload)
        with self._lock:
            pending = self._pending.get(ack.seq)
            if pending is None:
                return ack
            pending.protocol_acknowledged = True
            if pending.done and pending.ack is not None:
                return pending.ack
            pending.ack = ack
            if self._is_terminal(ack.status):
                pending.done = True
                pending.failed_reason = ""
                pending.terminal_event.set()
        return ack

    def poll(self) -> None:
        writes: list[bytes] = []
        with self._lock:
            now = self._now()
            for pending in self._pending.values():
                if pending.done or pending.protocol_acknowledged:
                    continue
                if now - pending.sent_at + 1e-9 < self._timeout:
                    continue
                if pending.retransmits >= self._max_retransmits:
                    pending.done = True
                    pending.failed_reason = "ack timeout"
                    pending.terminal_event.set()
                    continue
                writes.append(pending.frame_bytes)
                pending.retransmits += 1
                pending.sent_at = now
        for frame_bytes in writes:
            self._writer.write(frame_bytes)

    def reset_link(self, *, session: int) -> None:
        with self._lock:
            for pending in self._pending.values():
                if not pending.done:
                    pending.done = True
                    pending.failed_reason = "link reset"
                    pending.terminal_event.set()
            self._session = session
            self._pending.clear()
            self._latest_seq = None

    @staticmethod
    def _is_terminal(status: AckStatus) -> bool:
        return status in (
            AckStatus.REJECTED,
            AckStatus.COMPLETED,
            AckStatus.FAILED,
        )

    def _alloc_seq(self) -> int:
        seq = self._next_seq
        self._next_seq = 1 if self._next_seq >= 0xFFFF else self._next_seq + 1
        return seq


class RecentCommandCache:
    def __init__(self, max_items: int = 64):
        self._items: OrderedDict[tuple[int, int], bytes] = OrderedDict()
        self._max_items = max_items

    def get(self, session: int, seq: int) -> bytes | None:
        key = (session, seq)
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, session: int, seq: int, response_payload: bytes) -> None:
        key = (session, seq)
        self._items[key] = response_payload
        self._items.move_to_end(key)
        while len(self._items) > self._max_items:
            self._items.popitem(last=False)


class CommandValidator:
    def __init__(self) -> None:
        self.targets: tuple[int, int] | None = None
        self.stop_in_progress = False

    def validate(self, command: Command) -> RejectReason:
        if command.command_id == CommandId.PING:
            return RejectReason.NONE
        if command.command_id == CommandId.SET_TARGETS:
            if command.target1 is None or command.target2 is None:
                return RejectReason.BAD_PAYLOAD
            if not (1 <= command.target1 <= 12 and 1 <= command.target2 <= 12):
                return RejectReason.BAD_TARGETS
            if command.target1 == command.target2:
                return RejectReason.BAD_TARGETS
            return RejectReason.NONE
        if command.command_id == CommandId.STOP_MISSION:
            return RejectReason.NONE
        if command.command_id in (
            CommandId.START_MISSION,
            CommandId.START_VISION_ACQUIRE,
        ):
            return RejectReason.TARGETS_NOT_READY
        return RejectReason.UNKNOWN_COMMAND

    def apply_accepted(self, command: Command) -> bool:
        if command.command_id == CommandId.SET_TARGETS:
            assert command.target1 is not None and command.target2 is not None
            self.targets = (command.target1, command.target2)
            return True
        if command.command_id == CommandId.STOP_MISSION:
            if self.stop_in_progress:
                return False
            self.stop_in_progress = True
            return True
        return True
