from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
import struct
from typing import Iterable

from .models import FC_STATE_LAYOUT_V1, MAX_PAYLOAD_LEN, PROTOCOL_VERSION, MessageType


MAGIC = b"\xA5\x5A"
HEADER_STRUCT = struct.Struct(">2sBBB I H H")
CRC_STRUCT = struct.Struct(">H")
HMAC_LEN = 8
HEADER_LEN = HEADER_STRUCT.size
CRC_LEN = CRC_STRUCT.size
MIN_FRAME_LEN = HEADER_LEN + CRC_LEN + HMAC_LEN
FAST_TELEMETRY_MAGIC = b"\xC3\x3C"
FAST_TELEMETRY_VERSION = 1
FAST_TELEMETRY_HEADER = struct.Struct(">2sBBIH")
FAST_TELEMETRY_CORE = struct.Struct("<iiHBB")
FAST_TELEMETRY_LEN = FAST_TELEMETRY_HEADER.size + FAST_TELEMETRY_CORE.size + CRC_LEN


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    version: int
    msg_type: MessageType
    flags: int
    session: int
    seq: int
    payload: bytes


@dataclass(frozen=True)
class FastTelemetry:
    session: int
    seq: int
    payload: bytes


@dataclass
class ParserStats:
    crc_failures: int = 0
    hmac_failures: int = 0
    oversize_frames: int = 0
    version_failures: int = 0
    discarded_bytes: int = 0


def new_session() -> int:
    return secrets.randbits(32)


def crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _tag(data: bytes, key: bytes) -> bytes:
    if not key:
        raise ProtocolError("HMAC key is required")
    return hmac.new(key, data, hashlib.sha256).digest()[:HMAC_LEN]


def pack_frame(
    msg_type: MessageType,
    payload: bytes,
    *,
    session: int,
    seq: int,
    key: bytes,
    flags: int = 0,
    version: int = PROTOCOL_VERSION,
) -> bytes:
    if len(payload) > MAX_PAYLOAD_LEN:
        raise ProtocolError(f"payload too large: {len(payload)} > {MAX_PAYLOAD_LEN}")
    header = HEADER_STRUCT.pack(
        MAGIC,
        version,
        int(msg_type),
        flags & 0xFF,
        session & 0xFFFFFFFF,
        seq & 0xFFFF,
        len(payload),
    )
    protected = header + payload
    crc = CRC_STRUCT.pack(crc16_ccitt(protected))
    mac = _tag(protected + crc, key)
    return protected + crc + mac


def unpack_frame(frame_bytes: bytes, *, key: bytes) -> Frame:
    if len(frame_bytes) < MIN_FRAME_LEN:
        raise ProtocolError("frame too short")
    magic, version, msg_type, flags, session, seq, length = HEADER_STRUCT.unpack(
        frame_bytes[:HEADER_LEN]
    )
    if magic != MAGIC:
        raise ProtocolError("bad magic")
    if version != PROTOCOL_VERSION:
        raise ProtocolError("unsupported version")
    if length > MAX_PAYLOAD_LEN:
        raise ProtocolError("payload too large")
    expected_len = HEADER_LEN + length + CRC_LEN + HMAC_LEN
    if len(frame_bytes) != expected_len:
        raise ProtocolError("wrong frame length")
    payload = frame_bytes[HEADER_LEN : HEADER_LEN + length]
    crc_offset = HEADER_LEN + length
    expected_crc = CRC_STRUCT.unpack(frame_bytes[crc_offset : crc_offset + CRC_LEN])[0]
    actual_crc = crc16_ccitt(frame_bytes[:crc_offset])
    if expected_crc != actual_crc:
        raise ProtocolError("crc mismatch")
    expected_tag = frame_bytes[crc_offset + CRC_LEN :]
    actual_tag = _tag(frame_bytes[: crc_offset + CRC_LEN], key)
    if not hmac.compare_digest(expected_tag, actual_tag):
        raise ProtocolError("hmac mismatch")
    try:
        typed_msg = MessageType(msg_type)
    except ValueError as exc:
        raise ProtocolError("unknown message type") from exc
    return Frame(version, typed_msg, flags, session, seq, payload)


def pack_fast_telemetry(*, payload: bytes, session: int, seq: int) -> bytes:
    """Pack the fixed 24-byte, aircraft-to-ground telemetry frame.

    This frame is deliberately CRC-protected instead of HMAC-protected because it
    carries read-only flight data and must fit the HC-14's high-rate stream.
    Commands and acknowledgements always use ``pack_frame`` above.
    """
    if len(payload) != 13 or payload[0] != FC_STATE_LAYOUT_V1:
        raise ProtocolError("fast telemetry requires the compact FC_STATE core")
    header = FAST_TELEMETRY_HEADER.pack(
        FAST_TELEMETRY_MAGIC,
        FAST_TELEMETRY_VERSION,
        FC_STATE_LAYOUT_V1,
        session & 0xFFFFFFFF,
        seq & 0xFFFF,
    )
    protected = header + payload[1:]
    return protected + CRC_STRUCT.pack(crc16_ccitt(protected))


def unpack_fast_telemetry(frame_bytes: bytes) -> FastTelemetry:
    if len(frame_bytes) != FAST_TELEMETRY_LEN:
        raise ProtocolError("wrong fast telemetry length")
    magic, version, layout, session, seq = FAST_TELEMETRY_HEADER.unpack(
        frame_bytes[: FAST_TELEMETRY_HEADER.size]
    )
    if magic != FAST_TELEMETRY_MAGIC:
        raise ProtocolError("bad fast telemetry magic")
    if version != FAST_TELEMETRY_VERSION or layout != FC_STATE_LAYOUT_V1:
        raise ProtocolError("unsupported fast telemetry version")
    crc_offset = FAST_TELEMETRY_LEN - CRC_LEN
    expected_crc = CRC_STRUCT.unpack(frame_bytes[crc_offset:])[0]
    if expected_crc != crc16_ccitt(frame_bytes[:crc_offset]):
        raise ProtocolError("fast telemetry crc mismatch")
    return FastTelemetry(session, seq, bytes([layout]) + frame_bytes[FAST_TELEMETRY_HEADER.size:crc_offset])


class FrameParser:
    def __init__(self, *, key: bytes):
        if not key:
            raise ProtocolError("HMAC key is required")
        self._key = key
        self._buffer = bytearray()
        self.stats = ParserStats()

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while True:
            start = self._buffer.find(MAGIC)
            if start < 0:
                keep = 1 if self._buffer.endswith(MAGIC[:1]) else 0
                self.stats.discarded_bytes += len(self._buffer) - keep
                if keep:
                    del self._buffer[:-keep]
                else:
                    self._buffer.clear()
                return frames
            if start:
                self.stats.discarded_bytes += start
                del self._buffer[:start]
            if len(self._buffer) < HEADER_LEN:
                return frames

            try:
                _, version, _, _, _, _, length = HEADER_STRUCT.unpack(
                    self._buffer[:HEADER_LEN]
                )
            except struct.error:
                return frames

            if version != PROTOCOL_VERSION:
                self.stats.version_failures += 1
                del self._buffer[0]
                continue
            if length > MAX_PAYLOAD_LEN:
                self.stats.oversize_frames += 1
                del self._buffer[0]
                continue

            total_len = HEADER_LEN + length + CRC_LEN + HMAC_LEN
            if len(self._buffer) < total_len:
                return frames

            candidate = bytes(self._buffer[:total_len])
            del self._buffer[:total_len]
            try:
                frames.append(unpack_frame(candidate, key=self._key))
            except ProtocolError as exc:
                message = str(exc)
                if "crc" in message:
                    self.stats.crc_failures += 1
                elif "hmac" in message:
                    self.stats.hmac_failures += 1
                else:
                    self.stats.discarded_bytes += 1
        return frames


class FastTelemetryParser:
    """Fragment-tolerant parser for the one-way fixed-length telemetry stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.stats = ParserStats()

    def feed(self, data: bytes) -> list[FastTelemetry]:
        self._buffer.extend(data)
        frames: list[FastTelemetry] = []
        while True:
            start = self._buffer.find(FAST_TELEMETRY_MAGIC)
            if start < 0:
                keep = 1 if self._buffer.endswith(FAST_TELEMETRY_MAGIC[:1]) else 0
                self.stats.discarded_bytes += len(self._buffer) - keep
                if keep:
                    del self._buffer[:-keep]
                else:
                    self._buffer.clear()
                return frames
            if start:
                self.stats.discarded_bytes += start
                del self._buffer[:start]
            if len(self._buffer) < FAST_TELEMETRY_LEN:
                return frames
            candidate = bytes(self._buffer[:FAST_TELEMETRY_LEN])
            del self._buffer[:FAST_TELEMETRY_LEN]
            try:
                frames.append(unpack_fast_telemetry(candidate))
            except ProtocolError as exc:
                if "crc" in str(exc):
                    self.stats.crc_failures += 1
                else:
                    self.stats.discarded_bytes += 1


def split_bytes(data: bytes, sizes: Iterable[int]) -> list[bytes]:
    chunks: list[bytes] = []
    pos = 0
    for size in sizes:
        if pos >= len(data):
            break
        chunks.append(data[pos : pos + size])
        pos += size
    if pos < len(data):
        chunks.append(data[pos:])
    return chunks
