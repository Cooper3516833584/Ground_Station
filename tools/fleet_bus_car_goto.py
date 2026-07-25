"""Send one explicit FleetBus car navigation goal for supervised recovery."""

import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.fleet_models import (
    AckStatus,
    CarNavigateCommand,
    CommandId,
    CommandPayload,
    NodeId,
)
from components.fleet_protocol import decode_ack, encode_car_navigate
from components.half_duplex_master import HalfDuplexMaster, HalfDuplexTiming
from components.serial_transport import FCWirelessBridgeTransport


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("x_cm", type=int)
    parser.add_argument("y_cm", type=int)
    parser.add_argument("--heading-cdeg", type=int, default=None)
    parser.add_argument("--confirm-motion", action="store_true")
    parser.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
    )
    args = parser.parse_args(argv)
    if not args.confirm_motion:
        parser.error("--confirm-motion is required")

    holder = {}
    transport = FCWirelessBridgeTransport(
        port=args.port,
        baudrate=115200,
        on_bytes=lambda data: holder["master"].feed_bytes(data),
    )
    master = HalfDuplexMaster(
        transport=transport,
        timing=HalfDuplexTiming(command_retries=3),
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
        body = encode_car_navigate(
            CarNavigateCommand(args.x_cm, args.y_cm, args.heading_cdeg)
        )
        result = master.submit_command(
            NodeId.CAR,
            CommandPayload(CommandId.CAR_NAVIGATE_TO, command_body=body),
        ).result(6.0)
        if result.response is None:
            print("CAR GOTO TIMEOUT: {}".format(result.error))
            return 1
        ack = decode_ack(result.response.payload)
        print("CAR GOTO ACK: status={} reason={}".format(ack.status, ack.reason))
        return 0 if ack.status in (int(AckStatus.ACCEPTED), int(AckStatus.COMPLETED)) else 1
    finally:
        master.close()
        transport.stop()


if __name__ == "__main__":
    raise SystemExit(main())
