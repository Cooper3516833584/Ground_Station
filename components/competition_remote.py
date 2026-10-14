"""Screen START/STOP remote for the car decision centre; no mission execution."""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import json
import logging
import math
from pathlib import Path
import signal
import threading
import time
from typing import Callable

from .fleet_models import AckStatus, CommandId, CommandPayload, Frame, MessageKind, NodeId
from .fleet_protocol import FrameParser, SequenceCounter, decode_ack, encode_command, new_session, pack_frame


@dataclass(frozen=True)
class RemoteSettings:
    start_tokens: tuple[str, ...] = ("run",)
    stop_tokens: tuple[str, ...] = ("STOP",)
    response_timeout_seconds: float = 6.0
    command_retries: int = 3
    button_cooldown_seconds: float = 0.35
    log_screen_input: bool = True

    def __post_init__(self):
        groups = []
        for tokens in (self.start_tokens, self.stop_tokens):
            if not tokens or any(not isinstance(t, str) or not t.strip() or "\n" in t or "\r" in t for t in tokens):
                raise ValueError("button tokens must be nonempty single-line ASCII strings")
            groups.append({t.encode("ascii").strip().upper() for t in tokens})
        if groups[0] & groups[1]:
            raise ValueError("start and stop tokens must differ")
        for value in (self.response_timeout_seconds, self.button_cooldown_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("timeouts and cooldown must be positive finite numbers")
        if isinstance(self.command_retries, bool) or not isinstance(self.command_retries, int) or not 0 <= self.command_retries <= 10:
            raise ValueError("command_retries must be 0..10")
        if not isinstance(self.log_screen_input, bool):
            raise ValueError("log_screen_input must be boolean")


def load_remote_settings(path: str | Path) -> RemoteSettings:
    data = json.loads(Path(path).read_text(encoding="utf8"))
    if not isinstance(data, dict):
        raise ValueError("remote config must be an object")
    allowed = set(RemoteSettings.__dataclass_fields__)
    if set(data) - allowed:
        raise ValueError("unknown remote config keys: " + ", ".join(sorted(set(data)-allowed)))
    for key in ("start_tokens", "stop_tokens"):
        if key in data:
            if not isinstance(data[key], list):
                raise ValueError(key + " must be an array")
            data[key] = tuple(data[key])
    return RemoteSettings(**data)


class ScreenLineDecoder:
    """Match complete CR/LF-terminated messages; unknown bytes never trigger."""
    def __init__(self, settings: RemoteSettings, max_line_bytes: int = 256):
        self._tokens = {t.encode("ascii").strip().upper(): action
                        for action, tokens in (("START", settings.start_tokens), ("STOP", settings.stop_tokens))
                        for t in tokens}
        self._buffer = bytearray()
        self._discarding = False
        self._limit = max_line_bytes

    def feed(self, data: bytes) -> list[str]:
        actions = []
        for byte in data:
            if byte in (10, 13):
                if not self._discarding:
                    action = self._tokens.get(bytes(self._buffer).strip().upper())
                    if action:
                        actions.append(action)
                self._buffer.clear()
                self._discarding = False
            elif not self._discarding:
                self._buffer.append(byte)
                if len(self._buffer) > self._limit:
                    self._buffer.clear()
                    self._discarding = True
        return actions

    def reset(self):
        self._buffer.clear()
        self._discarding = False


@dataclass
class _Pending:
    action: str
    seq: int
    command_id: int
    frame: bytes
    ack: object | None = None
    cancelled: bool = False


class CarCommandRemote:
    """One writer, same-frame retries, STOP preemption, and CAR-only ACKs."""
    def __init__(self, transport, settings: RemoteSettings, emit: Callable, *, session: int | None = None):
        self.transport, self.settings, self.emit = transport, settings, emit
        self.session = new_session() if session is None else session
        self._seq = SequenceCounter()
        self._parser = FrameParser(local_node=NodeId.GROUND)
        self._condition = threading.Condition()
        self._queue = deque()
        self._pending: _Pending | None = None
        self._closing = False
        self._thread = None
        self._last_action = None
        self._last_at = -math.inf

    def start(self):
        self._thread = threading.Thread(target=self._run, name="competition-car-remote", daemon=True)
        self._thread.start()

    def close(self):
        with self._condition:
            self._closing = True
            self._queue.clear()
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=2)

    def submit(self, action: str) -> bool:
        if action not in ("START", "STOP"):
            raise ValueError("unknown screen action")
        now = time.monotonic()
        with self._condition:
            if self._closing:
                return False
            # A new STOP press also cancels START queued behind a pending STOP.
            if action == "STOP":
                self._queue = deque(item for item in self._queue if item == "STOP")
            same_pending = self._pending is not None and self._pending.action == action and not self._pending.cancelled
            if same_pending or action in self._queue:
                self.emit("button_coalesced", action=action)
                return False
            if action == self._last_action and now-self._last_at < self.settings.button_cooldown_seconds:
                self.emit("button_debounced", action=action)
                return False
            self._last_action, self._last_at = action, now
            if action == "STOP":
                self._queue.clear()
                if self._pending is not None and self._pending.action == "START":
                    self._pending.cancelled = True
                self._queue.appendleft(action)
            else:
                self._queue.append(action)
            self._condition.notify_all()
        self.emit("button_queued", action=action, destination=int(NodeId.CAR))
        return True

    def feed_bytes(self, data: bytes):
        for frame in self._parser.feed(data):
            if frame.src != NodeId.CAR or frame.kind != MessageKind.ACK:
                continue
            try:
                ack = decode_ack(frame.payload)
            except ValueError:
                continue
            with self._condition:
                pending = self._pending
                if (pending is None or pending.cancelled or ack.request_session != self.session
                        or ack.request_seq != pending.seq or ack.command_id != pending.command_id):
                    continue
                # A delayed RECEIVED must not overwrite a terminal acknowledgement.
                if pending.ack is None or pending.ack.status == AckStatus.RECEIVED:
                    pending.ack = ack
                self._condition.notify_all()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closing or bool(self._queue))
                if self._closing:
                    return
                action = self._queue.popleft()
                command_id = CommandId.CAR_START_MISSION if action == "START" else CommandId.TARGETED_STOP
                seq = self._seq.next()
                raw = pack_frame(Frame(1, NodeId.GROUND, NodeId.CAR, MessageKind.COMMAND, 0,
                                       self.session, seq, encode_command(CommandPayload(command_id))))
                pending = _Pending(action, seq, int(command_id), raw)
                self._pending = pending
            outcome = self._execute(pending)
            with self._condition:
                self._pending = None
                # An unconfirmed STOP must not be followed by a queued START.
                if action == "STOP" and outcome != "acknowledged":
                    removed = len(self._queue)
                    self._queue.clear()
                    if removed:
                        self.emit("queued_start_cancelled", reason="STOP not confirmed")

    def _execute(self, pending: _Pending) -> str:
        for attempt in range(1, self.settings.command_retries+2):
            with self._condition:
                if self._closing or pending.cancelled:
                    self.emit("command_cancelled", action=pending.action, seq=pending.seq)
                    return "cancelled"
            try:
                self.transport.write(pending.frame)
                self.emit("command_sent", action=pending.action, seq=pending.seq, attempt=attempt,
                          destination=int(NodeId.CAR), command_id=pending.command_id)
            except (OSError, RuntimeError) as exc:
                self.emit("radio_write_error", action=pending.action, seq=pending.seq, error=str(exc))
            deadline = time.monotonic()+self.settings.response_timeout_seconds
            with self._condition:
                while not self._closing and not pending.cancelled:
                    ack = pending.ack
                    if ack is not None and ack.status in (AckStatus.ACCEPTED, AckStatus.COMPLETED, AckStatus.REJECTED, AckStatus.FAILED):
                        outcome = "acknowledged" if ack.status in (AckStatus.ACCEPTED, AckStatus.COMPLETED) else "rejected"
                        self.emit("command_"+outcome, action=pending.action, seq=pending.seq,
                                  attempt=attempt, ack_status=int(ack.status), reason=int(ack.reason), detail=ack.detail)
                        return outcome
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
            if self._closing or pending.cancelled:
                self.emit("command_cancelled", action=pending.action, seq=pending.seq)
                return "cancelled"
        self.emit("command_timeout", action=pending.action, seq=pending.seq,
                  attempts=self.settings.command_retries+1)
        return "timeout"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station-config", default=None)
    parser.add_argument("--remote-config", default=str(Path(__file__).resolve().parents[1]/"config/competition_remote.example.json"))
    parser.add_argument("--observe-screen", action="store_true", help="record buttons without opening the radio or sending commands")
    parser.add_argument("--validate", action="store_true", help="validate configuration without opening hardware")
    args = parser.parse_args()
    from .station_config import load_station_settings
    station = load_station_settings(args.station_config)
    settings = load_remote_settings(args.remote_config)
    if Path(station.screen.port).resolve() == Path(station.fleet_radio.port).resolve():
        raise ValueError("screen and radio must use different devices")
    if args.validate:
        print("Competition remote configuration valid; no hardware opened")
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    def emit(event, **values):
        logging.info(json.dumps(dict(event=event, **values), ensure_ascii=False))
    from .serial_transport import HC14BridgeTransport, SerialTransport
    class ScreenTransport(SerialTransport):
        def _open_serial(self, serial_module):
            serial_obj = super()._open_serial(serial_module)
            try:
                serial_obj.reset_input_buffer()
            except Exception:
                serial_obj.close()
                raise
            return serial_obj
    decoder = ScreenLineDecoder(settings)
    stop = threading.Event()
    holder = {}
    status_window = None
    def screen_rx(data):
        if settings.log_screen_input:
            emit("screen_rx", hex=data[:256].hex(), text=data[:256].decode("ascii", "backslashreplace"))
        for action in decoder.feed(data):
            if status_window is not None:
                status_window.status_changed.emit(action)
            emit("screen_button", action=action)
            if not args.observe_screen:
                holder["remote"].submit(action)
    screen = ScreenTransport(port=station.screen.port, baudrate=station.screen.baudrate,
        read_timeout_seconds=station.screen.read_timeout_seconds, on_bytes=screen_rx,
        on_connected=lambda: (decoder.reset(), emit("screen_connected", port=station.screen.port)),
        on_disconnected=lambda exc: (decoder.reset(), emit("screen_disconnected", error=str(exc))))
    radio = remote = None
    if not args.observe_screen:
        radio = HC14BridgeTransport(port=station.fleet_radio.port, baudrate=station.fleet_radio.baudrate,
            read_timeout_seconds=station.fleet_radio.read_timeout_seconds,
            write_timeout_seconds=station.fleet_radio.write_timeout_seconds or .5,
            reconnect_seconds=station.fleet_radio.reconnect_seconds or 1.,
            on_bytes=lambda data: holder["remote"].feed_bytes(data),
            on_connected=lambda: emit("radio_connected", port=station.fleet_radio.port),
            on_disconnected=lambda exc: emit("radio_disconnected", error=str(exc)))
        remote = CarCommandRemote(radio, settings, emit)
        holder["remote"] = remote
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    if not args.observe_screen:
        from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
        from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

        class StatusWindow(QWidget):
            status_changed = pyqtSignal(str)

            def __init__(self):
                super().__init__()
                self.setWindowFlags(Qt.FramelessWindowHint)
                self.setStyleSheet("background-color: #101820; color: white;")
                layout = QVBoxLayout(self)
                label = QLabel("待机")
                label.setAlignment(Qt.AlignCenter)
                label.setStyleSheet("font-size: 72px; font-weight: bold;")
                layout.addWidget(label)
                self.label = label
                self.status_changed.connect(self.update_status, Qt.QueuedConnection)

            @pyqtSlot(str)
            def update_status(self, action):
                if action == "START":
                    self.label.setText("任务进行中")
                elif action == "STOP":
                    self.label.setText("待机")

        app = QApplication([])
        status_window = StatusWindow()
        stop_timer = QTimer()
        stop_timer.timeout.connect(lambda: app.quit() if stop.is_set() else None)
        stop_timer.start(100)
        status_window.showFullScreen()
    try:
        if remote:
            remote.start()
            radio.start()
        screen.start()
        emit("remote_ready", observe_screen=args.observe_screen, destination=int(NodeId.CAR),
             start_tokens=settings.start_tokens, stop_tokens=settings.stop_tokens)
        if args.observe_screen:
            stop.wait()
        else:
            app.exec_()
    finally:
        screen.stop()
        if remote:
            remote.close()
            radio.stop()
        emit("remote_closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
