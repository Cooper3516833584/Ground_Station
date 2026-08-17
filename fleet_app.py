"""Independent FleetBus V1 ground-station application."""

import argparse
import json
from pathlib import Path
import threading
import time


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "fleet_config.json"


def ground_owned_coordinate_sync_enabled(ground_owned_coordinate_frames):
    return not bool(ground_owned_coordinate_frames)


def field_target_to_local(
    frame_registry, node_id, x_cm, y_cm, heading_cdeg=None
):
    """Convert one FIELD target to integer FleetBus local-frame units."""
    transform = frame_registry.get(node_id)
    if transform is None:
        raise ValueError("该节点未配置 FIELD 坐标变换")
    local_x_cm, local_y_cm = transform.world_to_local_point(x_cm, y_cm)
    local_heading_cdeg = None
    if heading_cdeg is not None:
        local_heading_cdeg = int(
            round(
                transform.world_to_local_heading(float(heading_cdeg) / 100.0)
                * 100.0
            )
        ) % 36000
    return int(round(local_x_cm)), int(round(local_y_cm)), local_heading_cdeg


def load_config(path):
    with Path(path).open(encoding="utf-8") as handle:
        config = json.load(handle)
    forbidden = {"hmac", "key", "secret", "password"}

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key)
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    if any(word in key.lower() for key in keys(config) for word in forbidden):
        raise ValueError("FleetBus configuration must not contain key material")
    return config


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument(
        "--station-config",
        default=None,
        help="path to the station machine configuration "
        "(default: GROUND_STATION_CONFIG or config/station.local.json)",
    )
    return parser


def main():
    args = build_parser().parse_args()
    config = load_config(args.config)

    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication, QMessageBox

    from components.coordinate_frames import CoordinateFrameRegistry
    from components.fleet_models import (
        AckStatus,
        CarNavigateCommand,
        CommandId,
        CommandPayload,
        CoordinateFrameCommand,
        DisasterRescueCommand,
        DroneGotoCommand,
        NodeFlags,
        NodeId,
        SurveyFlags,
    )
    from components.fleet_protocol import (
        decode_ack,
        encode_car_navigate,
        encode_coordinate_frame,
        encode_disaster_rescue,
        encode_drone_goto,
    )
    from components.fleet_store import FleetStore
    from components.half_duplex_master import HalfDuplexMaster, HalfDuplexTiming
    from components.serial_transport import FCWirelessBridgeTransport
    from components.station_config import load_station_settings
    from components.trajectory_store import (
        TrajectoryStore,
        trajectory_policy_from_config,
    )
    from components.ui.fleet_main_window import FleetMainWindow

    station = load_station_settings(args.station_config)
    timing_config = config["timing"]
    ui_config = config["ui"]
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
    trajectory_policies = {
        int(NodeId.DRONE): trajectory_policy_from_config(ui_config, "drone"),
        int(NodeId.CAR): trajectory_policy_from_config(ui_config, "car"),
    }
    frame_registry = CoordinateFrameRegistry.from_config(
        config.get("coordinate_frames", {})
    )
    store = FleetStore(
        stale_seconds=max(1.5, timing.response_timeout_s * 2),
        offline_after_missed_polls=timing.offline_after_missed_polls,
        max_pose_jump_cm=timing_config.get("max_pose_jump_cm", 500.0),
        frame_registry=frame_registry,
    )
    store.trajectories = TrajectoryStore(
        (0x10, 0x20),
        max_points=ui_config["trajectory_max_points"],
        policies=trajectory_policies,
    )

    holder = {}
    transport = FCWirelessBridgeTransport(
        port=station.fleet_radio.port,
        baudrate=station.fleet_radio.baudrate,
        read_timeout_seconds=station.fleet_radio.read_timeout_seconds,
        write_timeout_seconds=(
            station.fleet_radio.write_timeout_seconds
            if station.fleet_radio.write_timeout_seconds is not None
            else 0.5
        ),
        reconnect_seconds=(
            station.fleet_radio.reconnect_seconds
            if station.fleet_radio.reconnect_seconds is not None
            else 1.0
        ),
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

    app = QApplication([])
    terrain_image_dir = Path(
        ui_config.get("terrain_image_directory", "assets/terrain")
    )
    if not terrain_image_dir.is_absolute():
        terrain_image_dir = ROOT / terrain_image_dir
    ground_owned_coordinate_frames = True
    window = FleetMainWindow(
        terrain_image_dir=terrain_image_dir,
        field_config=config.get("field", {}),
        display_geometry=config.get("display_geometry", {}),
        coordinate_frames=config.get("coordinate_frames", {}),
        ground_owned_coordinate_frames=ground_owned_coordinate_frames,
        coordinate_frames_confirmed=config.get(
            "coordinate_frames_confirmed", False
        ),
        trajectory_minimum_quality={
            node_id: policy.min_quality
            for node_id, policy in trajectory_policies.items()
        },
    )
    timer = QTimer()
    timer.setInterval(ui_config.get("snapshot_interval_milliseconds", 100))
    survey_timer = QTimer()
    survey_timer.setInterval(ui_config.get("survey_interval_milliseconds", 1000))
    rescue = {
        "future": None,
        "event_key": None,
        "completed": set(),
        "retry_after": 0.0,
    }

    def refresh_window_and_dispatch():
        snapshot = store.snapshot()
        window.update_snapshot(snapshot)
        future = rescue["future"]
        if future is not None:
            try:
                result = future.result(0)
            except TimeoutError:
                return
            rescue["future"] = None
            if result.response is not None:
                try:
                    ack = decode_ack(result.response.payload)
                except ValueError:
                    ack = None
                if ack is not None and ack.status in (
                    int(AckStatus.ACCEPTED), int(AckStatus.COMPLETED)
                ):
                    rescue["completed"].add(rescue["event_key"])
                    return
            rescue["retry_after"] = time.monotonic() + 2.0

        drone = snapshot.drone
        car = snapshot.car
        event_id = drone.wildfire_event_id
        event_key = (drone.session, event_id)
        if (
            not event_id
            or event_key in rescue["completed"]
            or not drone.survey_flags & int(SurveyFlags.COMPLETE)
            or time.monotonic() < rescue["retry_after"]
        ):
            return
        required = int(NodeFlags.READY | NodeFlags.MAP_READY | NodeFlags.POSE_VALID)
        if not car.online or car.stale or car.node_flags & required != required:
            return
        wire_event_id = ((drone.session or 0) ^ event_id) & 0xFFFF or event_id
        command = DisasterRescueCommand(
            wire_event_id,
            drone.wildfire_row,
            drone.wildfire_col,
            drone.terrain_codes,
        )
        rescue["event_key"] = event_key
        rescue["future"] = master.submit_command(
            NodeId.CAR,
            CommandPayload(
                CommandId.CAR_DISASTER_RESCUE,
                command_body=encode_disaster_rescue(command),
            ),
        )

    timer.timeout.connect(refresh_window_and_dispatch)
    survey_timer.timeout.connect(lambda: master.request_survey(NodeId.DRONE))

    def submit_command(node_id, command_id, body):
        payload = b""
        if command_id == int(CommandId.SET_COORDINATE_FRAME):
            if not ground_owned_coordinate_sync_enabled(
                ground_owned_coordinate_frames
            ):
                QMessageBox.warning(
                    window, "未下发", "FIELD 坐标由地面站管理，不同步到设备端。"
                )
                return
            payload = encode_coordinate_frame(CoordinateFrameCommand(*body))
        elif command_id == int(CommandId.CAR_NAVIGATE_TO):
            x_cm, y_cm, _height_cm, heading_cdeg = body
            try:
                x_cm, y_cm, heading_cdeg = field_target_to_local(
                    frame_registry, node_id, x_cm, y_cm, heading_cdeg
                )
            except ValueError as exc:
                QMessageBox.warning(window, "未下发", str(exc))
                return
            payload = encode_car_navigate(
                CarNavigateCommand(x_cm, y_cm, heading_cdeg)
            )
        elif command_id == int(CommandId.DRONE_GOTO):
            x_cm, y_cm, z_cm, heading_cdeg = body
            try:
                x_cm, y_cm, heading_cdeg = field_target_to_local(
                    frame_registry, node_id, x_cm, y_cm, heading_cdeg
                )
            except ValueError as exc:
                QMessageBox.warning(window, "未下发", str(exc))
                return
            payload = encode_drone_goto(
                DroneGotoCommand(x_cm, y_cm, z_cm, heading_cdeg)
            )
        master.submit_command(
            node_id, CommandPayload(command_id, command_body=payload)
        )

    window.command_requested.connect(submit_command)
    window.map_requested.connect(master.request_map)
    window.path_requested.connect(master.request_path)
    window.stop_all_requested.connect(
        lambda: threading.Thread(
            target=master.request_stop_all,
            name="fleet-stop-all-request",
            daemon=True,
        ).start()
    )

    closed = False

    def shutdown():
        nonlocal closed
        if closed:
            return
        closed = True
        timer.stop()
        survey_timer.stop()
        master.close()
        transport.stop()
        output = config.get("logging", {}).get("trajectory_csv_on_exit", "")
        if output:
            store.trajectories.export_csv(output)

    app.aboutToQuit.connect(shutdown)
    transport.start()
    master.start()
    timer.start()
    survey_timer.start()
    window.show()
    try:
        return app.exec_()
    finally:
        shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
