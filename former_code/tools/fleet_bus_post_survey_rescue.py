"""Run the supervised car rescue stage after the drone survey is complete."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.fleet_models import CommandId, CommandPayload, NodeId
from components.fleet_store import FleetStore
from components.half_duplex_master import HalfDuplexMaster, HalfDuplexTiming
from components.serial_transport import FCWirelessBridgeTransport
from components.screen_start_bridge import ScreenStartBridge


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "fleet_config.json"
DEFAULT_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-motion", action="store_true")
    parser.add_argument("--fleet-config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baudrate", type=int, default=115200)
    args = parser.parse_args(argv)
    if not args.confirm_motion:
        parser.error("--confirm-motion is required")

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
        port=args.port,
        baudrate=args.baudrate,
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
        cooldown_seconds=0.0,
    )

    bridge.start()
    try:
        bridge._wait_for_node("car", timeout=30.0)
        mapping_seq = bridge._send_command(
            NodeId.CAR,
            CommandPayload(CommandId.CAR_START_MAPPING),
        )
        print("Car mapping accepted (seq={})".format(mapping_seq), flush=True)
        bridge._wait_for_command_completion("car", mapping_seq, timeout=45.0)
        bridge._synchronize_car_frame()

        water_point, wildfire_point = bridge._car_rescue_points
        bridge._navigate_car(water_point, "water")
        bridge._hold_with_indicator("water")
        bridge._navigate_car(wildfire_point, "wildfire")
        bridge._hold_with_indicator("wildfire")
        bridge._navigate_car(bridge._car_start, "start")
        print("Post-survey car rescue completed", flush=True)
        return 0
    except (RuntimeError, TimeoutError, ValueError) as exc:
        print("Post-survey car rescue failed: {}".format(exc), flush=True)
        bridge._best_effort_stop_all()
        return 1
    finally:
        bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
