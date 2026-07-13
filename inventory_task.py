from __future__ import annotations

import argparse
import sys
import threading

from PyQt5 import QtCore, QtWidgets

from app import GroundStationController
from components.models import Command, CommandId, MissionState
from components.state_store import StateStore
from components.ui.main_window import MainWindow


class InventoryConsole(QtCore.QObject):
    line_received = QtCore.pyqtSignal(str)

    def __init__(
        self,
        application: QtWidgets.QApplication,
        controller: GroundStationController,
        store: StateStore,
    ):
        super().__init__()
        self._application = application
        self._controller = controller
        self._store = store
        self._known_items: dict[int, str] = {}
        self._last_state = MissionState.IDLE
        self._last_ack = ""
        self.line_received.connect(self._handle_line)
        self._controller.state_changed.connect(self._print_updates)

    def start(self) -> None:
        thread = threading.Thread(
            target=self._read_loop,
            name="inventory-ssh-console",
            daemon=True,
        )
        thread.start()

    def _read_loop(self) -> None:
        print("地面站盘点任务已就绪。输入 start 启动；盘点完成后输入 1~24 查询；输入 quit 退出。", flush=True)
        while True:
            try:
                line = input("ground> ")
            except (EOFError, KeyboardInterrupt):
                self.line_received.emit("quit")
                return
            self.line_received.emit(line.strip())

    @QtCore.pyqtSlot(str)
    def _handle_line(self, line: str) -> None:
        command = line.strip().lower()
        if command == "start":
            self._start_inventory()
            return
        if command in ("quit", "exit"):
            self._application.quit()
            return
        if command in ("help", "?"):
            print("命令：start 启动盘点；1~24 查询货物位置；quit 退出。", flush=True)
            return
        try:
            cargo_number = int(command)
        except ValueError:
            print("无效输入：请输入 start、1~24 或 quit。", flush=True)
            return
        if not 1 <= cargo_number <= 24:
            print("货物编号范围为 1~24。", flush=True)
            return
        location = self._store.inventory.location_for(cargo_number)
        if location is None:
            print(f"货物 {cargo_number}：尚未盘点到", flush=True)
        else:
            print(f"货物 {cargo_number}：{location}", flush=True)

    def _start_inventory(self) -> None:
        if self._store.mission.state in (
            MissionState.RUNNING,
            MissionState.STOPPING,
            MissionState.LANDING,
        ):
            print("盘点任务正在执行，不能重复启动。", flush=True)
            return
        if not self._controller.link.connected:
            print("HC-14 本地串口尚未连接，请检查地面站串口。", flush=True)
            return
        self._store.reset_inventory()
        self._known_items.clear()
        self._controller.link.enable_preflight_commands()
        try:
            seq = self._controller.link.send_command(Command(CommandId.START_MISSION))
        except RuntimeError as exc:
            print(f"启动指令发送失败：{exc}", flush=True)
            return
        self._store.last_command = CommandId.START_MISSION.name
        self._controller.state_changed.emit()
        print(f"已通过无线串口发送 start（seq={seq}），等待无人机确认。", flush=True)

    @QtCore.pyqtSlot()
    def _print_updates(self) -> None:
        for cargo_number, location in sorted(self._store.inventory.items.items()):
            if self._known_items.get(cargo_number) == location:
                continue
            self._known_items[cargo_number] = location
            print(
                f"盘点结果 {len(self._known_items):02d}/24：货物 {cargo_number} -> {location}",
                flush=True,
            )

        state = self._store.mission.state
        if state != self._last_state:
            self._last_state = state
            if state == MissionState.COMPLETED:
                print(
                    f"盘点完成，共记录 {len(self._store.inventory.items)}/24 件货物。"
                    "请输入货物编号查询位置。",
                    flush=True,
                )
            elif state == MissionState.FAILED:
                print(
                    f"盘点任务失败：{self._store.mission.message or '无人机未提供原因'}",
                    flush=True,
                )

        if self._store.last_ack and self._store.last_ack != self._last_ack:
            self._last_ack = self._store.last_ack
            print(f"无人机应答：{self._last_ack}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SSH-controlled inventory ground station")
    parser.add_argument(
        "--display-seconds",
        type=float,
        default=0.0,
        help="close the UI automatically after this many seconds",
    )
    parser.add_argument("--no-console", action="store_true")
    parser.add_argument("--windowed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    application = QtWidgets.QApplication(sys.argv)
    store = StateStore()
    controller = GroundStationController(store)
    window = MainWindow(store)
    controller.state_changed.connect(window.refresh)

    if args.windowed:
        window.show()
    else:
        window.showFullScreen()

    console = InventoryConsole(application, controller, store)
    if not args.no_console:
        console.start()
    if args.display_seconds > 0:
        QtCore.QTimer.singleShot(round(args.display_seconds * 1000), application.quit)

    controller.start()
    application.aboutToQuit.connect(controller.close)
    return application.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
