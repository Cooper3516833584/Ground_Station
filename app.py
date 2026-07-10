from __future__ import annotations

import sys

from PyQt5 import QtCore, QtWidgets

from config import load_settings
from ground_link import GroundStationLink
from models import AckStatus, Alarm, Command, CommandAck, CommandId, FCState, MessageType, MissionStatus
from state_store import StateStore
from ui.main_window import MainWindow


class GroundStationController(QtCore.QObject):
    state_changed = QtCore.pyqtSignal()

    def __init__(self, store: StateStore):
        super().__init__()
        self.settings = load_settings()
        self.store = store
        self.store.stale_after_seconds = self.settings.telemetry_stale_seconds
        self.link = GroundStationLink(
            port=self.settings.serial_port,
            baudrate=self.settings.baudrate,
            key=self.settings.hmac_key,
            command_timeout_seconds=self.settings.command_timeout_seconds,
            command_retries=self.settings.command_retries,
            on_fc_state=self.on_fc_state,
            on_mission_status=self.on_mission_status,
            on_ack=self.on_ack,
            on_alarm=self.on_alarm,
            on_connected=self.on_connected,
            on_disconnected=self.on_disconnected,
            on_activity=self.on_activity,
        )
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.on_timer)
        self.timer.start(100)

    def start(self) -> None:
        self.link.start()

    def close(self) -> None:
        self.link.close()

    def on_connected(self) -> None:
        self.store.link.connected = True
        self.store.link.alarm = ""
        self.state_changed.emit()

    def on_disconnected(self, exc: Exception | None) -> None:
        self.store.mark_disconnected(str(exc) if exc else "link disconnected")
        self.state_changed.emit()

    def on_activity(self, session: int, _msg_type: MessageType) -> None:
        self.store.note_link_activity(session=session)

    def on_fc_state(self, state: FCState, session: int) -> None:
        self.store.update_telemetry(state, session=session)
        self.state_changed.emit()

    def on_mission_status(self, status: MissionStatus, _session: int) -> None:
        self.store.update_mission(status)
        self.state_changed.emit()

    def on_ack(self, ack: CommandAck, _session: int) -> None:
        self.store.last_ack = (
            f"{ack.command_id.name} {ack.status.name}"
            if ack.reason.name == "NONE"
            else f"{ack.command_id.name} {ack.status.name}: {ack.reason.name}"
        )
        pending = self.link.pending_for_seq(ack.seq)
        if (
            pending is not None
            and ack.command_id == CommandId.SET_TARGETS
            and ack.status in (AckStatus.ACCEPTED, AckStatus.COMPLETED)
        ):
            self.store.mission.target1 = pending.command.target1
            self.store.mission.target2 = pending.command.target2
        self.state_changed.emit()

    def on_alarm(self, alarm: Alarm, _session: int) -> None:
        self.store.link.alarm = f"{alarm.code}: {alarm.message}"
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
        try:
            self.link.send_command(command)
            self.store.last_command = command.command_id.name
        except RuntimeError as exc:
            self.store.last_ack = f"local reject: {exc}"
        self.state_changed.emit()

    def set_targets(self, target1: int, target2: int) -> None:
        self.send_command(Command(CommandId.SET_TARGETS, target1, target2))

    def on_timer(self) -> None:
        try:
            self.link.poll()
        except RuntimeError:
            self.store.mark_disconnected("link disconnected")
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
    app.aboutToQuit.connect(controller.close)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
