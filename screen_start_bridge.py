from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import threading
import time

from components.led_control import GroundLedClient
from components.fleet_models import (
    AckStatus,
    CarNavigateCommand,
    CommandId,
    CommandPayload,
    CoordinateFrameCommand,
    DroneGotoCommand,
    NodeFlags,
    NodeId,
    SurveyFlags,
    TerrainCode,
)
from components.fleet_protocol import (
    decode_ack,
    encode_car_navigate,
    encode_coordinate_frame,
    encode_drone_goto,
)
from components.fleet_store import FleetStore
from components.half_duplex_master import HalfDuplexMaster, HalfDuplexTiming
from components.serial_transport import FCWirelessBridgeTransport


DEFAULT_SCREEN_PORT = (
    "/dev/serial/by-id/usb-jixin.pro_CMSIS-DAP_LU_LU_2022_8888-if00"
)
DEFAULT_SCREEN_BAUD = 9600
DEFAULT_HC14_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
WHITE_PIXELS = ((255, 255, 255),) * 7
# The boot-persistent GPIO18 daemon intentionally caps WS2812 brightness at 20.
WHITE_BRIGHTNESS = 20
DIM_WHITE_BRIGHTNESS = 3
DEFAULT_FLEET_CONFIG = Path(__file__).resolve().parent / "fleet_config.json"
SURVEY_X_CENTRES_CM = (115, 185, 255, 325, 395)
SURVEY_Y_CENTRES_CM = (175, 245, 315)


def survey_cell_to_global(
    row: int,
    col: int,
    cell_positions_cm: tuple[tuple[int, int], ...] = (),
) -> tuple[int, int]:
    if not 0 <= row < len(SURVEY_Y_CENTRES_CM) or not 0 <= col < len(
        SURVEY_X_CENTRES_CM
    ):
        raise ValueError("survey cell is outside the 3x5 grid")
    if cell_positions_cm:
        if len(cell_positions_cm) != 15:
            raise ValueError("survey absolute positions must contain 15 cells")
        return tuple(cell_positions_cm[row * 5 + col])
    return SURVEY_X_CENTRES_CM[col], SURVEY_Y_CENTRES_CM[row]


def nearest_water_global(
    terrain_codes: tuple[int, ...],
    start_global_cm: tuple[int, int],
    cell_positions_cm: tuple[tuple[int, int], ...] = (),
) -> tuple[int, int]:
    if len(terrain_codes) != 15:
        raise ValueError("survey terrain grid must contain 15 cells")
    water_codes = (int(TerrainCode.RIVER), int(TerrainCode.LAKE))
    candidates = []
    for row in range(3):
        for col in range(5):
            if int(terrain_codes[row * 5 + col]) not in water_codes:
                continue
            point = survey_cell_to_global(row, col, cell_positions_cm)
            distance2 = (point[0] - start_global_cm[0]) ** 2 + (
                point[1] - start_global_cm[1]
            ) ** 2
            candidates.append((distance2, row, col, point))
    if not candidates:
        raise ValueError("survey does not contain a lake or river")
    return min(candidates)[3]


def field_heading_to_math_ccw(field_heading_deg: float) -> float:
    """Convert field heading (0° up, clockwise-positive) to +X/CCW degrees."""
    if not math.isfinite(field_heading_deg):
        raise ValueError("field heading must be finite")
    return (90.0 - field_heading_deg) % 360.0


def drone_is_airborne(node, minimum_altitude_cm: int) -> bool:
    return bool(
        node.online
        and not node.stale
        and node.node_flags & int(NodeFlags.ARMED_OR_MOTOR_ACTIVE)
        and node.z_cm >= minimum_altitude_cm
    )


def command_result_timeout_s(timing: HalfDuplexTiming) -> float:
    """Cover one queued command plus this command's complete retry envelope."""
    attempts = timing.command_retries + 1
    transaction_budget = (
        attempts * timing.response_timeout_s
        + (attempts + 1) * timing.inter_slot_guard_s
    )
    return max(5.0, transaction_budget * 2.0 + 1.0)


class StartTokenDetector:
    """Detect case-insensitive START tokens, including tokens split across reads."""

    def __init__(self, token: bytes = b"START"):
        self._token = bytes(token).upper()
        self._buffer = bytearray()

    def feed(self, data: bytes) -> int:
        if not data:
            return 0
        self._buffer.extend(data.upper())
        count = 0
        while True:
            index = self._buffer.find(self._token)
            if index < 0:
                keep = max(0, len(self._token) - 1)
                if len(self._buffer) > keep:
                    del self._buffer[:-keep]
                return count
            count += 1
            del self._buffer[: index + len(self._token)]


class ScreenStartBridge:
    def __init__(
        self,
        *,
        transport: FCWirelessBridgeTransport,
        master: HalfDuplexMaster,
        store: FleetStore,
        mission_config: dict,
        cooldown_seconds: float,
        led: GroundLedClient | None = None,
    ):
        self._transport = transport
        self._master = master
        self._store = store
        self._led = GroundLedClient() if led is None else led
        self._cooldown_seconds = cooldown_seconds
        start = mission_config.get("car_start_global_cm", (0, 0))
        if not isinstance(start, (list, tuple)) or len(start) != 2:
            raise ValueError("car_start_global_cm must contain [x_cm, y_cm]")
        self._car_start = (round(float(start[0])), round(float(start[1])))
        rescue_points = mission_config.get(
            "car_rescue_points_cm", ((25, 105), (95, 175))
        )
        if (
            not isinstance(rescue_points, (list, tuple))
            or len(rescue_points) != 2
            or any(
                not isinstance(point, (list, tuple)) or len(point) != 2
                for point in rescue_points
            )
        ):
            raise ValueError(
                "car_rescue_points_cm must contain two [x_cm, y_cm] points"
            )
        self._car_rescue_points = tuple(
            (round(float(point[0])), round(float(point[1])))
            for point in rescue_points
        )
        self._car_start_field_heading_deg = float(
            mission_config.get("car_start_field_heading_deg", 0.0)
        )
        if not math.isfinite(self._car_start_field_heading_deg):
            raise ValueError("car_start_field_heading_deg must be finite")
        self._start_delay = max(
            0.0, float(mission_config.get("start_delay_seconds", 20.0))
        )
        self._takeoff_alarm_seconds = max(
            0.0, float(mission_config.get("takeoff_alarm_seconds", 5.0))
        )
        if self._takeoff_alarm_seconds > self._start_delay:
            raise ValueError(
                "takeoff_alarm_seconds must not exceed start_delay_seconds"
            )
        self._indicator_seconds = max(
            0.0, float(mission_config.get("indicator_seconds", 3.0))
        )
        self._drone_timeout = max(
            1.0,
            float(mission_config.get("drone_mission_timeout_seconds", 360.0)),
        )
        self._car_timeout = max(
            1.0,
            float(mission_config.get("car_navigation_timeout_seconds", 120.0)),
        )
        self._command_status_link_grace = max(
            2.0,
            float(
                mission_config.get(
                    "command_status_link_grace_seconds", 15.0
                )
            ),
        )
        self._airborne_altitude_cm = max(
            1,
            round(float(mission_config.get("drone_airborne_altitude_cm", 10))),
        )
        self._survey_interval = max(
            0.2, float(mission_config.get("survey_interval_seconds", 0.5))
        )
        self._lock = threading.Lock()
        self._start_in_progress = False
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_button_at = 0.0
        self._survey_future = None
        self._next_survey_at = 0.0
        self._survey_polling_enabled = False

    def start(self) -> None:
        self._stop.clear()
        self._set_flow("program ready")
        self._transport.start()
        self._master.start()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            active = self._start_in_progress
        if active:
            self._best_effort_stop_all()
        self._master.close()
        self._transport.stop()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=2.0)
        self._set_flow("task process stopped")

    def handle_screen_start(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_button_at < self._cooldown_seconds:
                print("Screen START ignored by cooldown", flush=True)
                return
            self._last_button_at = now
            if self._start_in_progress:
                print("Screen START ignored: mission already pending/running", flush=True)
                return
            if not self._transport.connected:
                print("Screen START ignored: HC-14 is not connected", flush=True)
                return
            self._start_in_progress = True
            self._worker = threading.Thread(
                target=self._run_disaster_survey,
                name="disaster-survey-coordinator",
                daemon=True,
            )
            self._worker.start()

    def tick(self) -> None:
        with self._lock:
            if not self._survey_polling_enabled:
                return
        future = self._survey_future
        if future is not None:
            try:
                future.result(0)
            except TimeoutError:
                return
            self._survey_future = None
            self._next_survey_at = time.monotonic() + self._survey_interval
        if time.monotonic() >= self._next_survey_at:
            self._survey_future = self._master.request_survey(NodeId.DRONE)

    def _run_disaster_survey(self) -> None:
        failure = None
        alarm_active = False
        sequence_started_at = time.monotonic()
        try:
            self._set_white_blink("screen START")
            self._wait_for_node("drone", timeout=20.0)
            prepare_seq = self._send_command(
                NodeId.DRONE,
                CommandPayload(CommandId.DRONE_PREPARE_MISSION),
            )
            print(
                f"Drone payload preparation accepted (seq={prepare_seq}); "
                "electromagnet engaged",
                flush=True,
            )
            self._wait_until(sequence_started_at + self._indicator_seconds)
            self._set_dim_white("startup indication complete")
            self._wait_until(
                sequence_started_at
                + self._start_delay
                - self._takeoff_alarm_seconds
            )

            self._wait_for_node("car", timeout=20.0)
            alarm_seq = self._send_command(
                NodeId.CAR,
                CommandPayload(CommandId.CAR_ALARM_ON),
            )
            alarm_active = True
            alarm_started_at = time.monotonic()
            print(
                f"Car takeoff alarm ON accepted (seq={alarm_seq})",
                flush=True,
            )
            self._wait_until(
                max(
                    sequence_started_at + self._start_delay,
                    alarm_started_at + self._takeoff_alarm_seconds,
                )
            )
            alarm_off_seq = self._send_command(
                NodeId.CAR,
                CommandPayload(CommandId.CAR_ALARM_OFF),
            )
            alarm_active = False
            print(
                f"Car takeoff alarm OFF accepted (seq={alarm_off_seq})",
                flush=True,
            )
            start_seq = self._send_command(
                NodeId.DRONE,
                CommandPayload(CommandId.DRONE_START_MISSION),
            )
            print(f"Drone mission START accepted (seq={start_seq})", flush=True)
            with self._lock:
                self._survey_polling_enabled = True
            mapping_seq = self._start_car_mapping_after_takeoff()
            self._wait_for_command_completion(
                "car", mapping_seq, timeout=45.0
            )
            self._synchronize_car_frame()
            self._wait_for_command_completion(
                "drone", start_seq, self._drone_timeout
            )
            survey = self._wait_for_complete_survey(timeout=20.0)

            wildfire_index = (
                survey.wildfire_row * 5 + survey.wildfire_col
            )
            if (
                not survey.wildfire_event_id
                or not 0 <= wildfire_index < len(survey.terrain_codes)
                or survey.terrain_codes[wildfire_index] != int(TerrainCode.WILDFIRE)
            ):
                raise RuntimeError("completed survey does not contain a valid wildfire")
            water_point, wildfire_point = self._car_rescue_points
            print(
                "Configured rescue targets in car startup frame: "
                f"start={self._car_start}, water={water_point}, "
                f"wildfire={wildfire_point}",
                flush=True,
            )

            self._navigate_car(water_point, "water")
            self._hold_with_indicator("water")
            self._navigate_car(wildfire_point, "wildfire")
            self._hold_with_indicator("wildfire")
            self._navigate_car(self._car_start, "start")
            print("Disaster survey and car rescue task completed", flush=True)
        except (RuntimeError, TimeoutError, ValueError) as exc:
            failure = exc
            print(f"Disaster survey task failed: {exc}", flush=True)
            self._best_effort_stop_all()
        finally:
            if alarm_active:
                self._best_effort_alarm_off()
            with self._lock:
                self._start_in_progress = False
                self._survey_polling_enabled = False
                self._survey_future = None
            self._set_flow("task failed" if failure is not None else "task completed")

    def _start_car_mapping_after_takeoff(self) -> int:
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            drone = self._wait_for_node("drone", timeout=2.0)
            if drone_is_airborne(drone, self._airborne_altitude_cm):
                print(
                    f"Drone airborne at {drone.z_cm}cm; starting car mapping",
                    flush=True,
                )
                self._wait_for_node("car", timeout=20.0)
                return self._send_command(
                    NodeId.CAR,
                    CommandPayload(CommandId.CAR_START_MAPPING),
                )
            self._wait(0.1)
        raise RuntimeError("drone did not become airborne; car mapping not started")

    def _wait(self, seconds: float) -> None:
        if self._stop.wait(max(0.0, seconds)):
            raise RuntimeError("task coordinator is stopping")

    def _wait_until(self, deadline: float) -> None:
        self._wait(max(0.0, deadline - time.monotonic()))

    def _wait_for_node(self, name: str, timeout: float, required_flags: int = 0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = self._store.snapshot()
            node = snapshot.drone if name == "drone" else snapshot.car
            if (
                node.online
                and not node.stale
                and node.node_flags & required_flags == required_flags
            ):
                return node
            self._wait(0.1)
        raise RuntimeError(f"{name} did not become ready within {timeout:.0f}s")

    def _send_command(self, node_id: int, command: CommandPayload) -> int:
        result = self._master.submit_command(node_id, command).result(
            command_result_timeout_s(self._master.timing)
        )
        if result.response is None:
            raise RuntimeError(
                f"{CommandId(command.command_id).name} response timeout: {result.error}"
            )
        ack = decode_ack(result.response.payload)
        if ack.status not in (int(AckStatus.ACCEPTED), int(AckStatus.COMPLETED)):
            raise RuntimeError(
                f"{CommandId(command.command_id).name} rejected "
                f"status={ack.status} reason={ack.reason} detail={ack.detail!r}"
            )
        return result.request.seq

    def _wait_for_command_completion(
        self, name: str, request_seq: int, timeout: float
    ) -> None:
        deadline = time.monotonic() + timeout
        unavailable_since = None
        while time.monotonic() < deadline:
            snapshot = self._store.snapshot()
            node = snapshot.drone if name == "drone" else snapshot.car
            now = time.monotonic()
            if node.online and not node.stale:
                unavailable_since = None
                if node.active_command_seq == request_seq:
                    if node.active_command_status == int(AckStatus.COMPLETED):
                        return
                    if node.active_command_status in (
                        int(AckStatus.REJECTED),
                        int(AckStatus.FAILED),
                    ):
                        raise RuntimeError(
                            f"{name} command {request_seq} failed with error "
                            f"{node.error_code}"
                        )
            elif unavailable_since is None:
                unavailable_since = now
            elif now - unavailable_since >= self._command_status_link_grace:
                raise RuntimeError(
                    f"{name} command status unavailable for "
                    f"{self._command_status_link_grace:.1f}s"
                )
            self._wait(0.1)
        raise RuntimeError(f"{name} command {request_seq} completion timeout")

    def _wait_for_complete_survey(self, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            survey = self._store.snapshot().drone
            if survey.survey_flags & int(SurveyFlags.COMPLETE):
                return survey
            result = self._master.request_survey(NodeId.DRONE).result(3.0)
            if result.response is not None:
                survey = self._store.snapshot().drone
                if survey.survey_flags & int(SurveyFlags.COMPLETE):
                    return survey
            self._wait(0.5)
        raise RuntimeError("drone survey report did not become complete")

    def _synchronize_car_frame(self) -> None:
        required = int(NodeFlags.READY | NodeFlags.MAP_READY | NodeFlags.POSE_VALID)
        car = self._wait_for_node("car", timeout=30.0, required_flags=required)
        if car.node_flags & int(NodeFlags.COORDINATE_FRAME_SYNCED):
            return
        internal_heading_deg = field_heading_to_math_ccw(
            self._car_start_field_heading_deg
        )
        heading_cdeg = round(internal_heading_deg * 100.0) % 36000
        body = encode_coordinate_frame(
            CoordinateFrameCommand(
                self._car_start[0], self._car_start[1], heading_cdeg
            )
        )
        self._send_command(
            NodeId.CAR,
            CommandPayload(CommandId.SET_COORDINATE_FRAME, command_body=body),
        )

    def _navigate_car(self, point: tuple[int, int], stage: str) -> None:
        required = int(
            NodeFlags.READY
            | NodeFlags.MAP_READY
            | NodeFlags.POSE_VALID
            | NodeFlags.COORDINATE_FRAME_SYNCED
        )
        self._wait_for_node("car", timeout=15.0, required_flags=required)
        body = encode_car_navigate(CarNavigateCommand(point[0], point[1], None))
        seq = self._send_command(
            NodeId.CAR,
            CommandPayload(CommandId.CAR_NAVIGATE_TO, command_body=body),
        )
        print(f"Car navigating to {stage} {point} without final heading", flush=True)
        self._wait_for_command_completion("car", seq, self._car_timeout)

    def _hold_with_indicator(self, stage: str) -> None:
        self._set_white_blink(f"car holding at {stage}")
        self._wait(self._indicator_seconds)
        self._set_dim_white(f"car {stage} hold complete")

    def _best_effort_stop_all(self) -> None:
        try:
            self._master.request_stop_all(timeout=2.0)
        except (RuntimeError, TimeoutError):
            pass

    def _best_effort_alarm_off(self) -> None:
        try:
            self._send_command(
                NodeId.CAR,
                CommandPayload(CommandId.CAR_ALARM_OFF),
            )
        except (RuntimeError, TimeoutError):
            pass

    def _set_white_blink(self, reason: str) -> None:
        try:
            self._led.blink(
                (255, 255, 255),
                brightness=WHITE_BRIGHTNESS,
                interval_seconds=0.25,
            )
            print(f"LED -> full white blink ({reason})", flush=True)
        except OSError as exc:
            print(f"LED blink unavailable: {exc}", flush=True)

    def _set_dim_white(self, reason: str) -> None:
        try:
            self._led.solid((255, 255, 255), brightness=DIM_WHITE_BRIGHTNESS)
            print(f"LED -> 3/255 white ({reason})", flush=True)
        except OSError as exc:
            print(f"LED dim white unavailable: {exc}", flush=True)

    def _set_flow(self, reason: str) -> None:
        try:
            self._led.flow()
            print(f"LED -> flow ({reason})", flush=True)
        except OSError as exc:
            print(f"LED flow unavailable: {exc}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Coordinate the drone survey and car rescue from screen START"
    )
    parser.add_argument("--screen-port", default=DEFAULT_SCREEN_PORT)
    parser.add_argument("--screen-baud", type=int, default=DEFAULT_SCREEN_BAUD)
    parser.add_argument("--hc14-port", default=DEFAULT_HC14_PORT)
    parser.add_argument("--hc14-baud", type=int, default=115200)
    parser.add_argument("--cooldown", type=float, default=0.75)
    parser.add_argument(
        "--fleet-config",
        default=str(DEFAULT_FLEET_CONFIG),
    )
    parser.add_argument("--log-raw", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    import serial
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from components.ui.fleet_main_window import FleetMainWindow

    with Path(args.fleet_config).open(encoding="utf-8") as handle:
        config = json.load(handle)
    timing_config = config["timing"]
    timing = HalfDuplexTiming(
        node_turnaround_s=timing_config["node_turnaround_seconds"],
        response_timeout_s=timing_config["response_timeout_seconds"],
        inter_slot_guard_s=timing_config["inter_slot_guard_seconds"],
        command_retries=timing_config["command_retries"],
        offline_after_missed_polls=timing_config["offline_after_missed_polls"],
        offline_poll_interval_s=timing_config.get(
            "offline_poll_interval_seconds", 5.0
        ),
    )
    store = FleetStore(
        stale_seconds=max(1.5, timing.response_timeout_s * 2),
        offline_after_missed_polls=timing.offline_after_missed_polls,
        max_pose_jump_cm=timing_config.get("max_pose_jump_cm", 500.0),
    )
    holder = {}
    transport = FCWirelessBridgeTransport(
        port=args.hc14_port,
        baudrate=args.hc14_baud,
        on_bytes=lambda data: holder["master"].feed_bytes(data),
        on_disconnected=lambda _error: store.mark_link_down(),
    )
    master = HalfDuplexMaster(
        transport=transport,
        timing=timing,
        on_frame=store.handle_frame,
        on_timeout=store.mark_timeout,
    )
    holder["master"] = master
    bridge = ScreenStartBridge(
        transport=transport,
        master=master,
        store=store,
        mission_config=config.get("disaster_survey", {}),
        cooldown_seconds=max(0.0, args.cooldown),
    )
    detector = StartTokenDetector()
    screen = serial.Serial()
    screen.port = args.screen_port
    screen.baudrate = args.screen_baud
    screen.bytesize = serial.EIGHTBITS
    screen.parity = serial.PARITY_NONE
    screen.stopbits = serial.STOPBITS_ONE
    screen.timeout = 0.05

    ui_config = config.get("ui", {})
    terrain_image_dir = Path(
        ui_config.get("terrain_image_directory", "assets/terrain")
    )
    if not terrain_image_dir.is_absolute():
        terrain_image_dir = Path(__file__).resolve().parent / terrain_image_dir
    app = QApplication([])
    window = FleetMainWindow(terrain_image_dir=terrain_image_dir)
    timer = QTimer()
    timer.setInterval(ui_config.get("snapshot_interval_milliseconds", 100))
    closed = False

    def submit_ui_command(node_id, command_id, body) -> None:
        payload = b""
        if command_id == int(CommandId.SET_COORDINATE_FRAME):
            payload = encode_coordinate_frame(CoordinateFrameCommand(*body))
        elif command_id == int(CommandId.CAR_NAVIGATE_TO):
            x_cm, y_cm, _height_cm, heading_cdeg = body
            payload = encode_car_navigate(
                CarNavigateCommand(x_cm, y_cm, heading_cdeg)
            )
        elif command_id == int(CommandId.DRONE_GOTO):
            x_cm, y_cm, z_cm, heading_cdeg = body
            payload = encode_drone_goto(
                DroneGotoCommand(x_cm, y_cm, z_cm, heading_cdeg)
            )
        master.submit_command(
            node_id, CommandPayload(command_id, command_body=payload)
        )

    def poll_screen_and_refresh() -> None:
        data = screen.read(screen.in_waiting or 1)
        if data and args.log_raw:
            print(f"SCREEN RX {data.hex(' ')} {data!r}", flush=True)
        for _ in range(detector.feed(data)):
            bridge.handle_screen_start()
        bridge.tick()
        window.update_snapshot(store.snapshot())

    def shutdown() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        timer.stop()
        if screen.is_open:
            screen.close()
        bridge.close()

    window.command_requested.connect(submit_ui_command)
    window.map_requested.connect(master.request_map)
    window.path_requested.connect(master.request_path)
    window.stop_all_requested.connect(
        lambda: threading.Thread(
            target=master.request_stop_all,
            kwargs={"timeout": 2.0},
            name="fleet-stop-all-request",
            daemon=True,
        ).start()
    )
    timer.timeout.connect(poll_screen_and_refresh)
    app.aboutToQuit.connect(shutdown)
    bridge.start()
    try:
        screen.open()
        print(
            f"Listening for screen START on {args.screen_port} @ {args.screen_baud}; "
            f"HC-14 {args.hc14_port} @ {args.hc14_baud}",
            flush=True,
        )
        window.show()
        timer.start()
        return app.exec_()
    except KeyboardInterrupt:
        print("Stopping screen START bridge", flush=True)
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
