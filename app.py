from __future__ import annotations

import sys

from PyQt5 import QtCore, QtWidgets

from command_service import CommandService
from config import load_settings
from models import Command, CommandId, FCState, MessageType
from protocol import FrameParser, new_session
from serial_transport import SerialTransport
from state_store import StateStore
from ui.main_window import MainWindow


class GroundStationController(QtCore.QObject):
    state_changed = QtCore.pyqtSignal()

    def __init__(self, store: StateStore):
        super().__init__()
        self.settings = load_settings()
        self.store = store
        self.session = new_session()
        self.parser = FrameParser(key=self.settings.hmac_key)
        self.transport = SerialTransport(
            port=self.settings.serial_port,
            baudrate=self.settings.baudrate,
            on_bytes=self.on_bytes,
            on_connected=self.on_connected,
            on_disconnected=self.on_disconnected,
        )
        self.command_service = CommandService(
            writer=self.transport,
            key=self.settings.hmac_key,
            session=self.session,
            timeout_seconds=self.settings.command_timeout_seconds,
            max_retries=self.settings.command_retries,
        )
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.on_timer)
        self.timer.start(100)

    def start(self) -> None:
        self.transport.start()

    def on_connected(self) -> None:
        self.store.link.connected = True
        self.store.link.alarm = ""
        self.state_changed.emit()

    def on_disconnected(self, exc: Exception | None) -> None:
        self.store.mark_disconnected(str(exc) if exc else "link disconnected")
        self.session = new_session()
        self.command_service.reset_link(session=self.session)
        self.state_changed.emit()

    def on_bytes(self, data: bytes) -> None:
        for frame in self.parser.feed(data):
            if frame.msg_type == MessageType.FC_STATE:
                self.store.update_telemetry(
                    FCState.from_payload(frame.payload), session=frame.session
                )
            elif frame.msg_type in (MessageType.COMMAND_ACK, MessageType.COMMAND_RESULT):
                ack = self.command_service.on_ack(frame)
                if ack is not None:
                    self.store.last_ack = (
                        f"{ack.command_id.name} {ack.status.name}"
                        if ack.reason.name == "NONE"
                        else f"{ack.command_id.name} {ack.status.name}: {ack.reason.name}"
                    )
            self.state_changed.emit()

    def send_command(self, command: Command) -> None:
        if command.command_id == CommandId.START_MISSION:
            reason = self.store.reject_reason_for_start()
            if reason.name != "NONE":
                self.store.last_ack = f"local reject: {reason.name}"
                self.state_changed.emit()
                return
        elif command.command_id == CommandId.START_VISION_ACQUIRE:
            reason = self.store.reject_reason_for_new_task()
            if reason.name != "NONE":
                self.store.last_ack = f"local reject: {reason.name}"
                self.state_changed.emit()
                return
        self.command_service.send(command)
        self.store.last_command = command.command_id.name
        self.state_changed.emit()

    def set_targets(self, target1: int, target2: int) -> None:
        self.send_command(Command(CommandId.SET_TARGETS, target1, target2))

    def on_timer(self) -> None:
        self.command_service.poll()
        if self.store.is_stale():
            self.store.link.alarm = "telemetry stale"
        self.state_changed.emit()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    store = StateStore()
    controller = GroundStationController(store)
    window = MainWindow(store)
    controller.state_changed.connect(window.refresh)
    window.command_requested.connect(controller.send_command)
    window.set_targets_requested.connect(controller.set_targets)
    window.show()
    controller.start()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
