"""Full-screen ground station for the coordinated road-patrol task."""

import argparse
import logging
from pathlib import Path

from land_air_app import load_config


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "d_task_fleet_config.json"


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    return parser


def main(argv=None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    config = load_config(args.config)

    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from components.fleet_store import FleetStore
    from components.half_duplex_master import HalfDuplexMaster, HalfDuplexTiming
    from components.ground_cue_player import GroundCuePlayer
    from components.road_patrol_coordinator import (
        RoadPatrolCoordinator,
        RoadPatrolTiming,
    )
    from components.serial_transport import FCWirelessBridgeTransport
    from components.ui.road_patrol_window import RoadPatrolWindow

    timing_config = config["timing"]
    timing = HalfDuplexTiming(
        node_turnaround_s=timing_config["node_turnaround_seconds"],
        response_timeout_s=timing_config["response_timeout_seconds"],
        inter_slot_guard_s=timing_config["inter_slot_guard_seconds"],
        command_retries=timing_config["command_retries"],
        offline_after_missed_polls=timing_config["offline_after_missed_polls"],
        offline_poll_interval_s=min(
            timing_config.get("offline_poll_interval_seconds", 5.0),
            timing_config["online_poll_interval_seconds"],
        ),
        online_poll_interval_s=timing_config["online_poll_interval_seconds"],
    )
    store = FleetStore(
        stale_seconds=max(1.5, timing.response_timeout_s * 2),
        offline_after_missed_polls=timing.offline_after_missed_polls,
        max_pose_jump_cm=timing_config.get("max_pose_jump_cm", 500.0),
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
    cue_player = GroundCuePlayer()
    coordinator = RoadPatrolCoordinator(
        master,
        store.snapshot,
        timing=RoadPatrolTiming.from_config(config.get("road_patrol")),
        cue_player=cue_player,
    )

    app = QApplication([])
    window = RoadPatrolWindow()
    refresh = QTimer()
    refresh.setInterval(
        config.get("ui", {}).get("snapshot_interval_milliseconds", 100)
    )
    refresh.timeout.connect(lambda: window.update_snapshot(store.snapshot()))

    closed = [False]

    def shutdown():
        if closed[0]:
            return
        closed[0] = True
        refresh.stop()
        coordinator.close()
        master.close()
        transport.stop()

    app.aboutToQuit.connect(shutdown)
    transport.start()
    master.start()
    coordinator.start()
    refresh.start()
    window.showFullScreen()
    try:
        return app.exec_()
    finally:
        shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
