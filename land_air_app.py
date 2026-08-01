"""Read-only FleetBus ground display for the D-task land-air collaboration."""

import argparse
import json
import logging
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
    timing = config.get("timing", {})
    if timing.get("online_poll_interval_seconds", 0) <= 0:
        raise ValueError("timing.online_poll_interval_seconds must be positive")
    trace = config.get("trace_sync", {})
    if trace:
        if trace.get("request_interval_seconds", 0) <= 0:
            raise ValueError("trace_sync.request_interval_seconds must be positive")
        if not 1 <= trace.get("max_samples_per_batch", 0) <= 15:
            raise ValueError("trace_sync.max_samples_per_batch must be in 1..15")
        if trace.get("max_catchup_batches", -1) < 0:
            raise ValueError("trace_sync.max_catchup_batches must not be negative")
        if trace.get("transaction_wait_timeout_seconds", 0) <= 0:
            raise ValueError(
                "trace_sync.transaction_wait_timeout_seconds must be positive"
            )
    return config


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    return parser


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args()
    config = load_config(args.config)

    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from components.coordinate_frames import CoordinateFrameRegistry
    from components.fleet_models import (
        AckStatus,
        CommandId,
        CommandPayload,
        MessageKind,
        NodeId,
    )
    from components.fleet_protocol import decode_ack
    from components.fleet_store import FleetStore
    from components.half_duplex_master import HalfDuplexMaster, HalfDuplexTiming
    from components.mission1_coordinator import (
        Mission1Coordinator,
        Mission1Timing,
    )
    from components.ground_cue_player import GroundCuePlayer
    from components.mission1_cue_controller import (
        Mission1CueController,
        Mission1CueTiming,
    )
    from components.mission2_cue_controller import (
        Mission2CueController,
        Mission2CueTiming,
    )
    from components.serial_transport import FCWirelessBridgeTransport
    from components.trace_sync import TraceSyncWorker
    from components.trajectory_store import (
        TrajectoryStore,
        trajectory_policy_from_config,
    )
    from components.ui.d_task_main_window import DTaskMainWindow

    timing_config = config["timing"]
    timing = HalfDuplexTiming(
        node_turnaround_s=timing_config["node_turnaround_seconds"],
        response_timeout_s=timing_config["response_timeout_seconds"],
        inter_slot_guard_s=timing_config["inter_slot_guard_seconds"],
        command_retries=timing_config["command_retries"],
        offline_after_missed_polls=timing_config["offline_after_missed_polls"],
        offline_poll_interval_s=timing_config.get("offline_poll_interval_seconds", 5.0),
        online_poll_interval_s=timing_config["online_poll_interval_seconds"],
    )
    ui_config = config["ui"]
    trajectory_policies = {
        int(NodeId.DRONE): trajectory_policy_from_config(ui_config, "drone"),
        int(NodeId.CAR): trajectory_policy_from_config(ui_config, "car"),
    }
    frames = CoordinateFrameRegistry.from_config(config.get("coordinate_frames", {}))
    store = FleetStore(
        stale_seconds=max(1.5, timing.response_timeout_s * 2),
        offline_after_missed_polls=timing.offline_after_missed_polls,
        max_pose_jump_cm=timing_config.get("max_pose_jump_cm", 500.0),
        frame_registry=frames,
    )
    store.trajectories = TrajectoryStore(
        (int(NodeId.DRONE), int(NodeId.CAR)),
        max_points=ui_config["trajectory_max_points"],
        policies=trajectory_policies,
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
    coordinator = Mission1Coordinator(
        master,
        store.snapshot,
        timing=Mission1Timing.from_config(
            config.get("mission1_coordination")
        ),
        cue_player=cue_player,
    )
    mission1_cue_controller = Mission1CueController(
        store.snapshot,
        cue_player=cue_player,
        timing=Mission1CueTiming.from_config(
            config.get("mission1_cues")
        ),
    )
    def switch_task2_cd_speed():
        future = master.submit_command(
            NodeId.CAR,
            CommandPayload(CommandId.CAR_SWITCH_TASK2_CD_SPEED),
        )
        result = future.result(5.0)
        if not result.succeeded or result.response is None:
            raise RuntimeError(
                "task 2 CD speed switch failed: {}".format(result.error)
            )
        if result.response.kind != int(MessageKind.ACK):
            raise RuntimeError("task 2 CD speed switch returned non-ACK")
        ack = decode_ack(result.response.payload)
        if ack.status != int(AckStatus.COMPLETED):
            raise RuntimeError(
                "task 2 CD speed switch rejected: status={} reason={}".format(
                    ack.status,
                    ack.reason,
                )
            )

    mission2_cue_controller = Mission2CueController(
        store.snapshot,
        cue_player=cue_player,
        retakeoff_succeeded_callback=switch_task2_cd_speed,
        timing=Mission2CueTiming.from_config(
            config.get("mission2_cues")
        ),
    )
    trace_config = config.get("trace_sync", {})
    trace_worker = None
    if (
        trace_config.get("enabled", False)
        and config.get("coordinate_frames_confirmed", False)
    ):
        trace_worker = TraceSyncWorker(
            master=master,
            store=store,
            node_ids=(int(NodeId.DRONE), int(NodeId.CAR)),
            request_interval_s=trace_config["request_interval_seconds"],
            max_samples=trace_config["max_samples_per_batch"],
            max_catchup_batches=trace_config["max_catchup_batches"],
            transaction_wait_timeout_s=trace_config[
                "transaction_wait_timeout_seconds"
            ],
        )

    app = QApplication([])
    window = DTaskMainWindow(
        field_config=config.get("field", {}),
        display_geometry=config.get("display_geometry", {}),
        coordinate_frames_confirmed=config.get(
            "coordinate_frames_confirmed", False
        ),
        trajectory_minimum_quality={
            node_id: policy.min_quality
            for node_id, policy in trajectory_policies.items()
        },
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
        mission2_cue_controller.close()
        mission1_cue_controller.close()
        coordinator.close()
        if trace_worker is not None:
            trace_worker.close()
        master.close()
        transport.stop()
        output = config.get("logging", {}).get("trajectory_csv_on_exit", "")
        if output:
            store.trajectories.export_csv(output)

    app.aboutToQuit.connect(shutdown)
    transport.start()
    master.start()
    if trace_worker is not None:
        trace_worker.start()
    coordinator.start()
    mission1_cue_controller.start()
    mission2_cue_controller.start()
    refresh.start()
    window.showFullScreen()
    try:
        return app.exec_()
    finally:
        shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
