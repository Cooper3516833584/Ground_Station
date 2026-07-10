from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
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
    done: bool = False
    failed_reason: str = ""


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
        self.pending: PendingCommand | None = None

    def send(self, command: Command) -> int:
        if self.pending and not self.pending.done:
            raise RuntimeError("another command is pending")
        seq = self._alloc_seq()
        frame_bytes = pack_frame(
            MessageType.COMMAND,
            command.to_payload(),
            session=self._session,
            seq=seq,
            key=self._key,
        )
        self._writer.write(frame_bytes)
        self.pending = PendingCommand(command, seq, frame_bytes, self._now())
        return seq

    def on_ack(self, frame: Frame) -> CommandAck | None:
        if frame.msg_type not in (MessageType.COMMAND_ACK, MessageType.COMMAND_RESULT):
            return None
        ack = CommandAck.from_payload(frame.payload)
        if self.pending is None or ack.seq != self.pending.seq:
            return ack
        self.pending.ack = ack
        if ack.status in (AckStatus.REJECTED, AckStatus.COMPLETED, AckStatus.FAILED):
            self.pending.done = True
        return ack

    def poll(self) -> None:
        if self.pending is None or self.pending.done:
            return
        now = self._now()
        if now - self.pending.sent_at + 1e-9 < self._timeout:
            return
        if self.pending.retransmits >= self._max_retransmits:
            self.pending.done = True
            self.pending.failed_reason = "ack timeout"
            return
        self._writer.write(self.pending.frame_bytes)
        self.pending.retransmits += 1
        self.pending.sent_at = now

    def reset_link(self, *, session: int) -> None:
        self._session = session
        self.pending = None

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
