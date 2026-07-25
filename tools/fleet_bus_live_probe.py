"""One-shot HC-14 FleetBus survey/PING probe with no motion commands."""

import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.fleet_models import CommandId, CommandPayload, NodeId
from components.fleet_protocol import decode_ack, decode_survey_report
from components.half_duplex_master import HalfDuplexMaster, HalfDuplexTiming
from components.serial_transport import FCWirelessBridgeTransport


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
    )
    parser.add_argument("--baudrate", type=int, default=115200)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    holder = {}
    transport = FCWirelessBridgeTransport(
        port=args.port,
        baudrate=args.baudrate,
        on_bytes=lambda data: holder["master"].feed_bytes(data),
    )
    master = HalfDuplexMaster(
        transport=transport,
        timing=HalfDuplexTiming(command_retries=1),
    )
    holder["master"] = master
    try:
        transport.start()
        deadline = time.monotonic() + 3.0
        while not transport.connected and time.monotonic() < deadline:
            time.sleep(0.05)
        if not transport.connected:
            raise RuntimeError("HC-14 serial port did not open")
        master.start()

        survey_result = master.request_survey(NodeId.DRONE).result(3.0)
        if survey_result.response is None:
            print("DRONE SURVEY TIMEOUT: {}".format(survey_result.error))
        else:
            survey = decode_survey_report(survey_result.response.payload)
            print(
                "DRONE SURVEY OK: revision={} flags={} wildfire=({}, {})".format(
                    survey.survey_revision,
                    survey.survey_flags,
                    survey.wildfire_row,
                    survey.wildfire_col,
                )
            )

        car_result = master.submit_command(
            NodeId.CAR, CommandPayload(CommandId.PING)
        ).result(4.0)
        if car_result.response is None:
            print("CAR PING TIMEOUT: {}".format(car_result.error))
        else:
            ack = decode_ack(car_result.response.payload)
            print("CAR PING OK: status={} reason={}".format(ack.status, ack.reason))

        print(
            "STATS: transactions={} responses={} timeouts={} late={} unexpected={}".format(
                master.stats.transactions,
                master.stats.responses,
                master.stats.timeouts,
                master.stats.late_frames,
                master.stats.unexpected_frames,
            )
        )
        return 0 if survey_result.succeeded and car_result.succeeded else 1
    finally:
        master.close()
        transport.stop()


if __name__ == "__main__":
    raise SystemExit(main())
