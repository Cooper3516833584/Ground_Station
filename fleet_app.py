"""Independent FleetBus V1 ground-station application."""

import argparse
import json
from pathlib import Path
import threading


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "fleet_config.json"


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
    return parser


def main():
    args = build_parser().parse_args()
    config = load_config(args.config)

    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from components.fleet_models import (
        CarNavigateCommand,
        CommandId,
        CommandPayload,
        CoordinateFrameCommand,
        DroneGotoCommand,
    )
    from components.fleet_protocol import (
        encode_car_navigate,
        encode_coordinate_frame,
        encode_drone_goto,
    )
    from components.fleet_store import FleetStore
    from components.half_duplex_master import HalfDuplexMaster, HalfDuplexTiming
    from components.serial_transport import FCWirelessBridgeTransport
    from components.ui.fleet_main_window import FleetMainWindow

    serial_config = config["serial"]
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
    store = FleetStore(
        stale_seconds=max(1.5, timing.response_timeout_s * 2),
        offline_after_missed_polls=timing.offline_after_missed_polls,
        max_pose_jump_cm=timing_config.get("max_pose_jump_cm", 500.0),
    )
    store.trajectories = store.trajectories.__class__(
        (0x10, 0x20),
        max_points=ui_config["trajectory_max_points"],
        min_distance_cm=ui_config["trajectory_min_distance_cm"],
    )

    holder = {}
    transport = FCWirelessBridgeTransport(
        port=serial_config["port"],
        baudrate=serial_config["baudrate"],
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
    window = FleetMainWindow()
    timer = QTimer()
    timer.setInterval(ui_config.get("snapshot_interval_milliseconds", 100))
    timer.timeout.connect(lambda: window.update_snapshot(store.snapshot()))

    def submit_command(node_id, command_id, body):
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
        master.close()
        transport.stop()
        output = config.get("logging", {}).get("trajectory_csv_on_exit", "")
        if output:
            store.trajectories.export_csv(output)

    app.aboutToQuit.connect(shutdown)
    transport.start()
    master.start()
    timer.start()
    window.show()
    try:
        return app.exec_()
    finally:
        shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
