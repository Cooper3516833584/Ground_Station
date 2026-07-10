from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct


PROTOCOL_VERSION = 1
MAX_PAYLOAD_LEN = 128


class MessageType(IntEnum):
    HEARTBEAT = 1
    FC_STATE = 2
    MISSION_STATUS = 3
    COMMAND = 4
    COMMAND_ACK = 5
    COMMAND_RESULT = 6
    ALARM = 7


class CommandId(IntEnum):
    PING = 1
    SET_TARGETS = 2
    START_MISSION = 3
    START_VISION_ACQUIRE = 4
    STOP_MISSION = 5


class AckStatus(IntEnum):
    RECEIVED = 1
    ACCEPTED = 2
    REJECTED = 3
    COMPLETED = 4
    FAILED = 5


class RejectReason(IntEnum):
    NONE = 0
    BAD_PAYLOAD = 1
    BAD_TARGETS = 2
    FC_OFFLINE = 3
    TASK_BUSY = 4
    TARGETS_NOT_READY = 5
    CAMERA_UNAVAILABLE = 6
    STALE_TELEMETRY = 7
    LINK_DOWN = 8
    UNKNOWN_COMMAND = 9


class MissionState(IntEnum):
    IDLE = 0
    ACQUIRING = 1
    READY = 2
    COUNTDOWN = 3
    RUNNING = 4
    STOPPING = 5
    LANDING = 6
    COMPLETED = 7
    FAILED = 8


FC_STATE_STRUCT = struct.Struct("<hhhii hhh ii H B ? B B B")


@dataclass(frozen=True)
class FCState:
    roll_deg: float
    pitch_deg: float
    yaw_deg: float
    alt_fused_cm: int
    alt_add_cm: int
    vel_x_cms: int
    vel_y_cms: int
    vel_z_cms: int
    pos_x_cm: int
    pos_y_cm: int
    battery_v: float
    mode: int
    unlock: bool
    cid: int
    cmd_0: int
    cmd_1: int

    @property
    def alt_fused_m(self) -> float:
        return self.alt_fused_cm / 100.0

    @property
    def alt_add_m(self) -> float:
        return self.alt_add_cm / 100.0

    @property
    def vel_x_ms(self) -> float:
        return self.vel_x_cms / 100.0

    @property
    def vel_y_ms(self) -> float:
        return self.vel_y_cms / 100.0

    @property
    def vel_z_ms(self) -> float:
        return self.vel_z_cms / 100.0

    @property
    def pos_x_m(self) -> float:
        return self.pos_x_cm / 100.0

    @property
    def pos_y_m(self) -> float:
        return self.pos_y_cm / 100.0

    def to_payload(self) -> bytes:
        return FC_STATE_STRUCT.pack(
            round(self.roll_deg / 0.01),
            round(self.pitch_deg / 0.01),
            round(self.yaw_deg / 0.01),
            self.alt_fused_cm,
            self.alt_add_cm,
            self.vel_x_cms,
            self.vel_y_cms,
            self.vel_z_cms,
            self.pos_x_cm,
            self.pos_y_cm,
            round(self.battery_v / 0.01),
            self.mode,
            self.unlock,
            self.cid,
            self.cmd_0,
            self.cmd_1,
        )

    @classmethod
    def from_payload(cls, payload: bytes) -> "FCState":
        if len(payload) != FC_STATE_STRUCT.size:
            raise ValueError(f"FC_STATE payload must be {FC_STATE_STRUCT.size} bytes")
        values = FC_STATE_STRUCT.unpack(payload)
        return cls(
            roll_deg=values[0] * 0.01,
            pitch_deg=values[1] * 0.01,
            yaw_deg=values[2] * 0.01,
            alt_fused_cm=values[3],
            alt_add_cm=values[4],
            vel_x_cms=values[5],
            vel_y_cms=values[6],
            vel_z_cms=values[7],
            pos_x_cm=values[8],
            pos_y_cm=values[9],
            battery_v=values[10] * 0.01,
            mode=values[11],
            unlock=values[12],
            cid=values[13],
            cmd_0=values[14],
            cmd_1=values[15],
        )


@dataclass(frozen=True)
class Command:
    command_id: CommandId
    target1: int | None = None
    target2: int | None = None

    def to_payload(self) -> bytes:
        if self.command_id == CommandId.SET_TARGETS:
            if self.target1 is None or self.target2 is None:
                raise ValueError("SET_TARGETS requires target1 and target2")
            return bytes([self.command_id, self.target1, self.target2])
        return bytes([self.command_id])

    @classmethod
    def from_payload(cls, payload: bytes) -> "Command":
        if not payload:
            raise ValueError("empty command payload")
        try:
            command_id = CommandId(payload[0])
        except ValueError as exc:
            raise ValueError("unknown command id") from exc
        if command_id == CommandId.SET_TARGETS:
            if len(payload) != 3:
                raise ValueError("SET_TARGETS payload must be 3 bytes")
            return cls(command_id, payload[1], payload[2])
        if len(payload) != 1:
            raise ValueError("command payload has extra bytes")
        return cls(command_id)


ACK_STRUCT = struct.Struct(">BBHBB")


@dataclass(frozen=True)
class CommandAck:
    message_type: MessageType
    command_id: CommandId
    seq: int
    status: AckStatus
    reason: RejectReason = RejectReason.NONE

    def to_payload(self) -> bytes:
        return ACK_STRUCT.pack(
            self.message_type,
            self.command_id,
            self.seq,
            self.status,
            self.reason,
        )

    @classmethod
    def from_payload(cls, payload: bytes) -> "CommandAck":
        if len(payload) != ACK_STRUCT.size:
            raise ValueError(f"ACK payload must be {ACK_STRUCT.size} bytes")
        msg_type, command_id, seq, status, reason = ACK_STRUCT.unpack(payload)
        return cls(
            MessageType(msg_type),
            CommandId(command_id),
            seq,
            AckStatus(status),
            RejectReason(reason),
        )

