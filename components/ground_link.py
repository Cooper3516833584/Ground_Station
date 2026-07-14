from __future__ import annotations

import threading
import time
from queue import Empty, Full, Queue
from typing import Callable

from .command_service import CommandService, PendingCommand
from .models import (
    Alarm,
    Command,
    CommandAck,
    FCState,
    LEDControl,
    FLAG_UPLINK_WINDOW,
    MessageType,
    MissionStatus,
)
from .protocol import FrameParser, new_session, pack_frame
from .serial_transport import FCWirelessBridgeTransport


class GroundStationLink:
    """Ground-side HC-14 send/receive component.

    Serial parsing stays in the transport thread. Parsed objects are delivered via
    callbacks so Qt code can forward them through signals without sharing framing
    or retry logic with the UI.
    """

    def __init__(
        self,
        *,
        port: str,
        key: bytes,
        baudrate: int = 115200,
        command_timeout_seconds: float = 0.8,
        command_retries: int = 3,
        uplink_delay_seconds: float = 0.0,
        wake_repeats: int = 1,
        wake_interval_seconds: float = 0.015,
        wake_settle_seconds: float = 0.015,
        command_burst_count: int = 2,
        command_burst_interval_seconds: float = 0.02,
        on_fc_state: Callable[[FCState, int], None] | None = None,
        on_mission_status: Callable[[MissionStatus, int], None] | None = None,
        on_ack: Callable[[CommandAck, int], None] | None = None,
        on_alarm: Callable[[Alarm, int], None] | None = None,
        on_led_control: Callable[[LEDControl, int], None] | None = None,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[Exception | None], None] | None = None,
        on_activity: Callable[[int, MessageType], None] | None = None,
        transport_factory=FCWirelessBridgeTransport,
    ):
        if not key:
            raise ValueError("HMAC key is required")
        self._key = key
        self._session = new_session()
        self._parser = FrameParser(key=key)
        self._telemetry_session: int | None = None
        self._telemetry_seq: int | None = None
        self._last_telemetry_time = 0.0
        self._uplink_delay_seconds = uplink_delay_seconds
        self._wake_repeats = wake_repeats
        self._wake_interval_seconds = wake_interval_seconds
        self._wake_settle_seconds = wake_settle_seconds
        self._command_burst_count = command_burst_count
        self._command_burst_interval_seconds = command_burst_interval_seconds
        self._last_rx_time = 0.0
        self._uplink_generation = 0
        self._direct_command_mode = True
        self._rx_condition = threading.Condition()
        self._tx_queue: Queue[bytes | None] = Queue(maxsize=16)
        self._queued_frames: set[bytes] = set()
        self._queue_lock = threading.Lock()
        self._tx_stop = threading.Event()
        self._tx_thread: threading.Thread | None = None
        self._connected = False
        self._connection_lock = threading.Lock()
        self._on_fc_state = on_fc_state
        self._on_mission_status = on_mission_status
        self._on_ack = on_ack
        self._on_alarm = on_alarm
        self._on_led_control = on_led_control
        self._on_connected_callback = on_connected
        self._on_disconnected_callback = on_disconnected
        self._on_activity = on_activity
        self._transport = transport_factory(
            port=port,
            baudrate=baudrate,
            on_bytes=self._on_bytes,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
        )
        self._commands = CommandService(
            writer=self,
            key=key,
            session=self._session,
            timeout_seconds=command_timeout_seconds,
            max_retries=command_retries,
        )

    @property
    def session(self) -> int:
        return self._session

    @property
    def connected(self) -> bool:
        with self._connection_lock:
            return self._connected

    @property
    def pending(self) -> PendingCommand | None:
        return self._commands.pending

    def pending_for_seq(self, seq: int) -> PendingCommand | None:
        return self._commands.pending_for_seq(seq)

    def enable_preflight_commands(self) -> None:
        """Allow direct command bursts while the aircraft is listening pre-flight."""
        with self._rx_condition:
            self._direct_command_mode = True
            self._rx_condition.notify_all()

    def disable_commands_for_flight(self) -> None:
        """Prevent uplink transmission while the aircraft is telemetry-only."""
        with self._rx_condition:
            self._direct_command_mode = False
            self._rx_condition.notify_all()

    def wait_for_terminal(
        self, seq: int, timeout: float | None = None
    ) -> PendingCommand | None:
        return self._commands.wait_for_terminal(seq, timeout)

    def start(self) -> None:
        if self._tx_thread is not None and self._tx_thread.is_alive():
            return
        self._tx_stop.clear()
        self._tx_thread = threading.Thread(
            target=self._tx_loop, name="ground-station-uplink", daemon=True
        )
        self._tx_thread.start()
        self._transport.start()

    def close(self) -> None:
        self._tx_stop.set()
        try:
            self._tx_queue.put_nowait(None)
        except Full:
            pass
        with self._rx_condition:
            self._rx_condition.notify_all()
        self._transport.stop()
        if self._tx_thread is not None:
            self._tx_thread.join(timeout=2.0)

    def send_command(self, command: Command) -> int:
        if not self.connected:
            raise RuntimeError("HC-14 link is not connected")
        return self._commands.send(command)

    def write(self, data: bytes) -> None:
        if not self.connected:
            raise RuntimeError("HC-14 link is not connected")
        with self._queue_lock:
            if data in self._queued_frames:
                return
            try:
                self._tx_queue.put_nowait(data)
            except Full as exc:
                raise RuntimeError("HC-14 uplink queue is full") from exc
            self._queued_frames.add(data)

    def poll(self) -> None:
        self._commands.poll()

    def _on_connected(self) -> None:
        with self._connection_lock:
            self._connected = True
        if self._on_connected_callback is not None:
            self._on_connected_callback()

    def _on_disconnected(self, exc: Exception | None) -> None:
        with self._connection_lock:
            self._connected = False
        self._clear_tx_queue()
        self._session = new_session()
        self._parser = FrameParser(key=self._key)
        self._telemetry_session = None
        self._telemetry_seq = None
        self._last_telemetry_time = 0.0
        self._commands.reset_link(session=self._session)
        if self._on_disconnected_callback is not None:
            self._on_disconnected_callback(exc)

    def _on_bytes(self, data: bytes) -> None:
        with self._rx_condition:
            self._last_rx_time = time.monotonic()
        for frame in self._parser.feed(data):
            try:
                if frame.msg_type == MessageType.FC_STATE:
                    if not self._accept_telemetry(frame.session, frame.seq):
                        continue
                    if frame.flags & FLAG_UPLINK_WINDOW:
                        with self._rx_condition:
                            self._uplink_generation += 1
                            self._rx_condition.notify_all()
                if self._on_activity is not None:
                    self._on_activity(frame.session, frame.msg_type)
                if frame.msg_type == MessageType.FC_STATE:
                    if self._on_fc_state is not None:
                        self._on_fc_state(FCState.from_payload(frame.payload), frame.session)
                elif frame.msg_type == MessageType.MISSION_STATUS:
                    if self._on_mission_status is not None:
                        self._on_mission_status(
                            MissionStatus.from_payload(frame.payload), frame.session
                        )
                elif frame.msg_type in (
                    MessageType.COMMAND_ACK,
                    MessageType.COMMAND_RESULT,
                ):
                    ack = self._commands.on_ack(frame)
                    if ack is not None and self._on_ack is not None:
                        self._on_ack(ack, frame.session)
                elif frame.msg_type == MessageType.ALARM:
                    if self._on_alarm is not None:
                        self._on_alarm(Alarm.from_payload(frame.payload), frame.session)
                elif frame.msg_type == MessageType.LED_CONTROL:
                    if self._on_led_control is not None:
                        self._on_led_control(
                            LEDControl.from_payload(frame.payload), frame.session
                        )
            except (UnicodeDecodeError, ValueError):
                continue

    def _accept_telemetry(self, session: int, seq: int) -> bool:
        now = time.monotonic()
        if self._telemetry_session != session:
            if (
                self._telemetry_session is not None
                and now - self._last_telemetry_time <= 1.5
            ):
                return False
            self._telemetry_session = session
            self._telemetry_seq = None
        if self._telemetry_seq is not None:
            delta = (seq - self._telemetry_seq) & 0xFFFF
            if delta == 0 or delta > 0x8000:
                return False
        self._telemetry_seq = seq
        self._last_telemetry_time = now
        return True

    def _tx_loop(self) -> None:
        while not self._tx_stop.is_set():
            try:
                data = self._tx_queue.get(timeout=0.1)
            except Empty:
                continue
            if data is None:
                return
            try:
                if not self._wait_for_uplink_window():
                    continue
                wake_frame = pack_frame(
                    MessageType.HEARTBEAT,
                    b"\x01",
                    session=self._session,
                    seq=0,
                    key=self._key,
                )
                for _ in range(self._wake_repeats):
                    self._transport.write(wake_frame)
                    if self._tx_stop.wait(self._wake_interval_seconds):
                        return
                if self._tx_stop.wait(self._wake_settle_seconds):
                    return
                for _ in range(self._command_burst_count):
                    self._transport.write(data)
                    if self._tx_stop.wait(self._command_burst_interval_seconds):
                        return
            except RuntimeError:
                pass
            finally:
                with self._queue_lock:
                    self._queued_frames.discard(data)

    def _wait_for_uplink_window(self) -> bool:
        with self._rx_condition:
            if self._direct_command_mode:
                return True
            generation = self._uplink_generation
            opened = self._rx_condition.wait_for(
                lambda: self._tx_stop.is_set()
                or self._uplink_generation != generation,
                timeout=2.5,
            )
            if not opened or self._tx_stop.is_set():
                return False
            last_rx_time = self._last_rx_time
        delay = self._uplink_delay_seconds - (time.monotonic() - last_rx_time)
        if delay > 0:
            self._tx_stop.wait(delay)
        return not self._tx_stop.is_set()

    def _clear_tx_queue(self) -> None:
        with self._queue_lock:
            self._queued_frames.clear()
        while True:
            try:
                self._tx_queue.get_nowait()
            except Empty:
                return
