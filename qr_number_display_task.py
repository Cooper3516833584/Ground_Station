from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import threading

from PyQt5 import QtCore, QtGui, QtWidgets

from app import GroundStationController
from components.config import load_hmac_key
from components.models import Command, CommandId, LEDControl, LEDMode, MissionState
from components.state_store import StateStore
from screen_start_bridge import (
    DEFAULT_HC14_PORT,
    DEFAULT_SCREEN_BAUD,
    DEFAULT_SCREEN_PORT,
    StartTokenDetector,
    WHITE_BRIGHTNESS,
    WHITE_PIXELS,
)


def extract_numeric_qr(message: str) -> str | None:
    if not message.startswith("QR:"):
        return None
    value = message[3:]
    if not value or not value.isascii() or not value.isdigit():
        return None
    return value


class NumberDisplayWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("二维码数字显示")
        self.setStyleSheet("background-color: black; color: white;")
        self._label = QtWidgets.QLabel("点击 START 开始", self)
        self._label.setAlignment(QtCore.Qt.AlignCenter)
        self._label.setWordWrap(False)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addWidget(self._label)
        self._fit_text()

    def show_text(self, text: str) -> None:
        self._label.setText(text)
        self._fit_text()

    def show_number(self, number: str) -> None:
        self.show_text(number)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_text()

    def _fit_text(self) -> None:
        text = self._label.text() or " "
        width = max(1, self._label.width() - 40)
        height = max(1, self._label.height() - 40)
        low, high = 12, 1000
        best = low
        font = QtGui.QFont("DejaVu Sans")
        font.setBold(True)
        while low <= high:
            size = (low + high) // 2
            font.setPixelSize(size)
            bounds = QtGui.QFontMetrics(font).boundingRect(text)
            if bounds.width() <= width and bounds.height() <= height:
                best = size
                low = size + 1
            else:
                high = size - 1
        font.setPixelSize(best)
        self._label.setFont(font)


class ScreenStartReader(QtCore.QObject):
    start_pressed = QtCore.pyqtSignal()
    failed = QtCore.pyqtSignal(str)

    def __init__(self, port: str, baudrate: int):
        super().__init__()
        self._port = port
        self._baudrate = baudrate
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._read_loop,
            name="qr-display-screen-reader",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _read_loop(self) -> None:
        import serial

        detector = StartTokenDetector()
        serial_obj = serial.Serial()
        serial_obj.port = self._port
        serial_obj.baudrate = self._baudrate
        serial_obj.bytesize = serial.EIGHTBITS
        serial_obj.parity = serial.PARITY_NONE
        serial_obj.stopbits = serial.STOPBITS_ONE
        serial_obj.timeout = 0.05
        try:
            serial_obj.open()
            print(
                f"Listening for screen START on {self._port} @ {self._baudrate}",
                flush=True,
            )
            while not self._stop.is_set():
                data = serial_obj.read(serial_obj.in_waiting or 1)
                for _ in range(detector.feed(data)):
                    self.start_pressed.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if serial_obj.is_open:
                serial_obj.close()


class QRDisplayCoordinator(QtCore.QObject):
    def __init__(
        self,
        controller: GroundStationController,
        store: StateStore,
        window: NumberDisplayWindow,
        screen_reader: ScreenStartReader,
    ):
        super().__init__()
        self._controller = controller
        self._store = store
        self._window = window
        self._screen_reader = screen_reader
        self._active = False
        self._stopping = False
        self._last_ack = ""
        self._last_message = ""
        self._last_connected: bool | None = None
        screen_reader.start_pressed.connect(self.toggle)
        screen_reader.failed.connect(self._screen_failed)
        controller.state_changed.connect(self._state_changed)

    @QtCore.pyqtSlot()
    def toggle(self) -> None:
        if self._stopping:
            return
        if self._active:
            self._stop_session()
        else:
            self._start_session()

    def close(self) -> None:
        self._screen_reader.close()
        if self._active and self._controller.link.connected:
            self._controller.link.enable_preflight_commands()
            try:
                seq = self._controller.link.send_command(
                    Command(CommandId.STOP_MISSION)
                )
            except RuntimeError:
                pass
            else:
                pending = self._controller.link.wait_for_terminal(seq, timeout=1.5)
                if pending is None or not pending.done:
                    print(
                        "STOP_MISSION was not confirmed before shutdown",
                        flush=True,
                    )
        self._set_flow()

    def _start_session(self) -> None:
        reason = self._store.reject_reason_for_new_task()
        if reason.name != "NONE":
            self._window.show_text(f"暂时无法启动\n{reason.name}")
            return
        self._set_white()
        self._controller.link.enable_preflight_commands()
        self._active = True
        self._window.show_text("正在启动摄像头…")
        try:
            seq = self._controller.link.send_command(
                Command(CommandId.START_VISION_ACQUIRE)
            )
        except RuntimeError as exc:
            self._active = False
            self._set_flow()
            self._window.show_text(f"启动失败\n{exc}")
            return
        self._store.last_command = CommandId.START_VISION_ACQUIRE.name
        print(f"START_VISION_ACQUIRE sent (seq={seq})", flush=True)

    def _stop_session(self) -> None:
        if not self._controller.link.connected:
            self._window.show_text("无线已断开\n无法结束任务")
            return
        self._controller.link.enable_preflight_commands()
        self._stopping = True
        self._window.show_text("正在结束任务…")
        try:
            seq = self._controller.link.send_command(Command(CommandId.STOP_MISSION))
        except RuntimeError as exc:
            self._stopping = False
            self._window.show_text(f"结束失败，请重试\n{exc}")
            return
        self._store.last_command = CommandId.STOP_MISSION.name
        print(f"STOP_MISSION sent (seq={seq})", flush=True)

    @QtCore.pyqtSlot(str)
    def _screen_failed(self, detail: str) -> None:
        self._window.show_text(f"START 按键串口不可用\n{detail}")
        print(f"Screen serial failed: {detail}", flush=True)

    @QtCore.pyqtSlot()
    def _state_changed(self) -> None:
        connected = self._store.link.connected
        if connected != self._last_connected:
            self._last_connected = connected
            if not connected and self._active:
                self._window.show_text("无线已断开\n任务状态未知")

        message = self._store.mission.message
        if message and message != self._last_message:
            self._last_message = message
            number = extract_numeric_qr(message)
            if number is not None and self._active:
                self._window.show_number(number)
                print(f"QR number displayed: {number}", flush=True)
            elif message == "QR:SCANNING" and self._active:
                self._window.show_text("正在识别二维码")
            elif message == "QR:STOPPED":
                self._finish_session("点击 START 再次开始")

        ack = self._store.last_ack
        if ack and ack != self._last_ack:
            self._last_ack = ack
            print(f"Aircraft ACK: {ack}", flush=True)
            if ack.startswith(CommandId.START_VISION_ACQUIRE.name):
                if " ACCEPTED" in ack and self._active:
                    self._window.show_text("正在识别二维码")
                elif " REJECTED" in ack or " FAILED" in ack:
                    self._finish_session(f"启动失败\n{ack}")
            elif ack.startswith(CommandId.STOP_MISSION.name):
                if " ACCEPTED" in ack:
                    self._set_flow()
                if " COMPLETED" in ack:
                    self._finish_session("点击 START 再次开始")
                elif " FAILED" in ack or " REJECTED" in ack:
                    self._stopping = False
                    self._window.show_text(f"结束失败，请重试\n{ack}")

        if self._store.mission.state == MissionState.FAILED and self._active:
            self._finish_session("摄像头任务失败\n点击 START 重试")

    def _finish_session(self, text: str) -> None:
        self._active = False
        self._stopping = False
        self._controller.link.enable_preflight_commands()
        self._set_flow()
        self._window.show_text(text)

    def _set_white(self) -> None:
        try:
            self._controller.led_client.apply(
                LEDControl(
                    LEDMode.PIXELS,
                    brightness=WHITE_BRIGHTNESS,
                    pixels=WHITE_PIXELS,
                )
            )
        except OSError as exc:
            print(f"LED white unavailable: {exc}", flush=True)

    def _set_flow(self) -> None:
        try:
            self._controller.led_client.flow()
        except OSError as exc:
            print(f"LED flow unavailable: {exc}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent ground QR number display task")
    parser.add_argument("--screen-port", default=DEFAULT_SCREEN_PORT)
    parser.add_argument("--screen-baud", type=int, default=DEFAULT_SCREEN_BAUD)
    parser.add_argument("--hc14-port", default=DEFAULT_HC14_PORT)
    parser.add_argument("--hc14-baud", type=int, default=115200)
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--display-seconds", type=float, default=0.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent
    os.environ["GROUND_STATION_SERIAL_PORT"] = args.hc14_port
    os.environ["GROUND_STATION_BAUDRATE"] = str(args.hc14_baud)
    if not os.getenv("GROUND_STATION_HMAC_KEY_HEX"):
        os.environ["GROUND_STATION_HMAC_KEY_HEX"] = load_hmac_key(
            key_file=root / "config" / "secrets" / "hmac.key"
        ).hex()

    application = QtWidgets.QApplication(sys.argv)
    application.setApplicationName("二维码数字显示")
    store = StateStore()
    controller = GroundStationController(store)
    window = NumberDisplayWindow()
    screen_reader = ScreenStartReader(args.screen_port, args.screen_baud)
    coordinator = QRDisplayCoordinator(controller, store, window, screen_reader)
    window._qr_display_coordinator = coordinator

    if args.windowed:
        window.resize(1000, 600)
        window.show()
    else:
        window.showFullScreen()
    if args.display_seconds > 0:
        QtCore.QTimer.singleShot(round(args.display_seconds * 1000), application.quit)

    controller.start()
    screen_reader.start()
    application.aboutToQuit.connect(coordinator.close)
    application.aboutToQuit.connect(controller.close)
    return application.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
