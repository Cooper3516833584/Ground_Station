"""Read-only FleetBus ground display for the D-task land-air collaboration."""

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "d_task_fleet_config.json"


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

    from components.coordinate_frames import CoordinateFrameRegistry
    from components.fleet_models import NodeId
    from components.fleet_store import FleetStore
    from components.half_duplex_master import HalfDuplexMaster, HalfDuplexTiming
    from components.serial_transport import FCWirelessBridgeTransport
    from components.ui.d_task_main_window import DTaskMainWindow

    timing_config = config["timing"]
    timing = HalfDuplexTiming(
        node_turnaround_s=timing_config["node_turnaround_seconds"],
        response_timeout_s=timing_config["response_timeout_seconds"],
        inter_slot_guard_s=timing_config["inter_slot_guard_seconds"],
        command_retries=timing_config["command_retries"],
        offline_after_missed_polls=timing_config["offline_after_missed_polls"],
        offline_poll_interval_s=timing_config.get("offline_poll_interval_seconds", 5.0),
    )
    ui_config = config["ui"]
    frames = CoordinateFrameRegistry.from_config(config.get("coordinate_frames", {}))
    store = FleetStore(
        stale_seconds=max(1.5, timing.response_timeout_s * 2),
        offline_after_missed_polls=timing.offline_after_missed_polls,
        max_pose_jump_cm=timing_config.get("max_pose_jump_cm", 500.0),
        frame_registry=frames,
    )
    store.trajectories = store.trajectories.__class__(
        (int(NodeId.DRONE), int(NodeId.CAR)),
        max_points=ui_config["trajectory_max_points"],
        min_distance_cm=ui_config["trajectory_min_distance_cm"],
    )

    holder = {}
    serial_config = config["serial"]
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
    window = DTaskMainWindow(
        field_config=config.get("field", {}),
        display_geometry=config.get("display_geometry", {}),
        coordinate_frames_confirmed=config.get(
            "coordinate_frames_confirmed", False
        ),
    )
    refresh = QTimer()
    refresh.setInterval(ui_config.get("snapshot_interval_milliseconds", 100))
    refresh.timeout.connect(lambda: window.update_snapshot(store.snapshot()))

    closed = [False]

    def shutdown():
        if closed[0]:
            return
        closed[0] = True
        refresh.stop()
        master.close()
        transport.stop()
        output = config.get("logging", {}).get("trajectory_csv_on_exit", "")
        if output:
            store.trajectories.export_csv(output)

    app.aboutToQuit.connect(shutdown)
    transport.start()
    master.start()
    refresh.start()
    window.show()
    try:
        return app.exec_()
    finally:
        shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
