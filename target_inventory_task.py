from __future__ import annotations

import argparse
import sys
import threading

from PyQt5 import QtCore, QtWidgets

from app import GroundStationController
from components.models import Command, CommandId
from components.state_store import StateStore
from components.ui.target_route_window import TargetRouteWindow
from target_routes import (
    TargetMissionEvent,
    cycle_location_codes,
    parse_location,
    parse_target_message,
)


class RouteCycler(QtCore.QObject):
    def __init__(
        self,
        window: TargetRouteWindow,
        interval_seconds: float = 3.0,
        rounds: int = 3,
    ):
        super().__init__(window)
        self._window = window
        self._rounds = rounds
        self._interval_seconds = interval_seconds
        self._codes = cycle_location_codes(rounds)
        self._index = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(round(interval_seconds * 1000))
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        self._show_current()
        self._timer.start()

    @QtCore.pyqtSlot()
    def _advance(self) -> None:
        self._index += 1
        if self._index >= len(self._codes):
            self._timer.stop()
            self._window.set_cycle_complete(self._rounds)
            return
        self._show_current()

    def _show_current(self) -> None:
        self._window.select_target(self._codes[self._index])
        round_number = self._index // 24 + 1
        position = self._index % 24 + 1
        self._window.set_cycle_status(
            round_number,
            self._rounds,
            position,
            self._interval_seconds,
        )


class TargetInventoryConsole(QtCore.QObject):
    line_received = QtCore.pyqtSignal(str)

    def __init__(
        self,
        application: QtWidgets.QApplication,
        controller: GroundStationController,
        store: StateStore,
        window: TargetRouteWindow,
    ):
        super().__init__()
        self._application = application
        self._controller = controller
        self._store = store
        self._window = window
        self._started = False
        self._quit_requested = False
        self._last_message = ""
        self._last_ack = ""
        self._last_connected: bool | None = None
        self.line_received.connect(self._handle_line)
        self._controller.state_changed.connect(self._handle_state_change)

    def start(self) -> None:
        thread = threading.Thread(
            target=self._read_loop,
            name="target-inventory-ssh-console",
            daemon=True,
        )
        thread.start()

    def _read_loop(self) -> None:
        print(
            "任务二地面站已就绪。输入 start 开始扫描摄像头0；输入 quit 取消并退出。",
            flush=True,
        )
        while True:
            try:
                line = input("target> ")
            except (EOFError, KeyboardInterrupt):
                self.line_received.emit("quit")
                return
            self.line_received.emit(line.strip())
            if line.strip().lower() in ("quit", "exit"):
                return

    @QtCore.pyqtSlot(str)
    def _handle_line(self, line: str) -> None:
        command = line.strip().lower()
        if command == "start":
            self._start_acquisition()
            return
        if command in ("quit", "exit"):
            self._request_quit()
            return
        if command in ("help", "?"):
            print("命令：start 开始无限扫描；quit 中止扫描并退出。", flush=True)
            return
        if command:
            print("无效输入，请输入 start 或 quit。", flush=True)

    def _start_acquisition(self) -> None:
        if self._started:
            print("任务二已经启动，不能重复发送 start。", flush=True)
            return
        if not self._controller.link.connected:
            print("HC-14 地面站串口尚未连接，请检查串口。", flush=True)
            return
        self._controller.link.enable_preflight_commands()
        try:
            seq = self._controller.link.send_command(
                Command(CommandId.START_VISION_ACQUIRE)
            )
        except RuntimeError as exc:
            print(f"启动指令发送失败：{exc}", flush=True)
            return
        self._started = True
        self._store.last_command = CommandId.START_VISION_ACQUIRE.name
        self._window.show_scanning()
        self._controller.state_changed.emit()
        print(
            f"已发送 start（seq={seq}）。飞机正在持续扫描摄像头0，"
            "识别不限时；输入 quit 可取消。",
            flush=True,
        )

    def _request_quit(self) -> None:
        if self._quit_requested:
            return
        self._quit_requested = True
        if self._started and self._controller.link.connected:
            try:
                seq = self._controller.link.send_command(
                    Command(CommandId.STOP_MISSION)
                )
                print(f"已发送 quit/STOP（seq={seq}），等待飞机确认。", flush=True)
                self._window.set_operational_status("正在取消摄像头扫描…")
                QtCore.QTimer.singleShot(2500, self._application.quit)
                return
            except RuntimeError as exc:
                print(f"STOP 发送失败：{exc}", flush=True)
        self._application.quit()

    @QtCore.pyqtSlot()
    def _handle_state_change(self) -> None:
        connected = self._store.link.connected
        if connected != self._last_connected:
            self._last_connected = connected
            print(
                "HC-14 无线串口已连接。" if connected else "HC-14 无线串口已断开。",
                flush=True,
            )
            if not self._started:
                self._window.set_operational_status(
                    "无线串口已连接 · SSH 输入 start"
                    if connected
                    else "等待无线串口连接"
                )

        message = self._store.mission.message
        if message and message != self._last_message:
            self._last_message = message
            event = parse_target_message(message)
            if event is not None:
                self._handle_target_event(event)

        ack = self._store.last_ack
        if ack and ack != self._last_ack:
            self._last_ack = ack
            print(f"飞机应答：{ack}", flush=True)
            if (
                ack.startswith(CommandId.START_VISION_ACQUIRE.name)
                and (" REJECTED" in ack or " FAILED" in ack)
            ):
                self._started = False
                self._window.set_operational_status(f"启动失败 · {ack}")

    def _handle_target_event(self, event: TargetMissionEvent) -> None:
        cargo = event.cargo_number
        location = event.location
        if location is not None:
            self._window.select_target(location)
        if event.kind == "DETECTED":
            text = f"识别成功：货物 {cargo} 位于 {location}，10 秒后起飞。"
        elif event.kind == "COUNTDOWN":
            text = f"货物 {cargo} → {location} · {event.seconds} 秒后起飞"
        elif event.kind == "TAKEOFF":
            text = f"无人机已起飞，正在前往 {location}"
        elif event.kind == "ARRIVED":
            text = f"已到达 {location}，正在视觉对准二维码"
        elif event.kind == "VERIFIED":
            text = f"盘点成功：货物 {cargo} → {location}"
        elif event.kind == "LANDING":
            text = "目标盘点完成，正在前往降落点"
        elif event.kind == "COMPLETE":
            text = f"任务二完成：货物 {cargo} → {location}，无人机已降落"
            self._started = False
        else:
            text = f"任务二失败：{event.detail}"
            self._started = False
        self._window.set_operational_status(text)
        print(text, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Task 2 target inventory route UI")
    parser.add_argument(
        "--target",
        default="A1",
        type=lambda value: parse_location(value).code,
        help="initial shelf location (A1-D6)",
    )
    parser.add_argument("--windowed", action="store_true", help="do not enter full screen")
    parser.add_argument(
        "--display-seconds",
        type=float,
        default=0.0,
        help="close the UI automatically after this many seconds",
    )
    parser.add_argument(
        "--auto-cycle",
        action="store_true",
        help="show A1-D6 automatically",
    )
    parser.add_argument(
        "--cycle-seconds",
        type=float,
        default=3.0,
        help="seconds to show each route",
    )
    parser.add_argument(
        "--cycle-rounds",
        type=int,
        default=3,
        help="number of A1-D6 cycles",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    application = QtWidgets.QApplication(sys.argv)
    application.setApplicationName("定向盘点航线")
    window = TargetRouteWindow(args.target)
    if args.windowed:
        window.show()
    else:
        window.showFullScreen()
    if args.auto_cycle:
        cycler = RouteCycler(window, args.cycle_seconds, args.cycle_rounds)
        window._route_cycler = cycler
        cycler.start()
    else:
        store = StateStore()
        controller = GroundStationController(store)
        console = TargetInventoryConsole(
            application,
            controller,
            store,
            window,
        )
        window._ground_controller = controller
        window._target_console = console
        console.start()
        controller.start()
        application.aboutToQuit.connect(controller.close)
    if args.display_seconds > 0:
        QtCore.QTimer.singleShot(round(args.display_seconds * 1000), application.quit)
    return application.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
