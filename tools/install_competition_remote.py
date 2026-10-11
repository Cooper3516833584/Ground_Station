"""Install the screen remote systemd service; run as root after Git deployment."""
import argparse
from datetime import datetime
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default="cooper")
    parser.add_argument("--screen-port", required=True)
    parser.add_argument("--radio-port", required=True)
    args = parser.parse_args()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        parser.error("run as root using sudo")
    account = pwd.getpwnam(args.user)
    group = grp.getgrgid(account.pw_gid).gr_name
    root = Path(__file__).resolve().parents[1]
    if any(c in str(root) for c in ('"', "\\", "%", "\n")):
        parser.error("unsupported project path for systemd")
    for port in (args.screen_port, args.radio_port):
        if not Path(port).is_absolute() or not Path(port).exists():
            parser.error("serial device must exist: " + port)
    if Path(args.screen_port).resolve() == Path(args.radio_port).resolve():
        parser.error("screen and radio cannot share a device")
    # Stop before changing system configuration. No tracked repository file is written.
    subprocess.run(["systemctl", "stop", "competition-remote.service"], check=False)
    config_dir = Path("/etc/robocup-ground-station")
    config_dir.mkdir(parents=True, exist_ok=True)
    station_path = config_dir / "station.json"
    remote_path = config_dir / "competition_remote.json"
    unit_path = Path("/etc/systemd/system/competition-remote.service")
    backup_dir = Path("/var/backups/robocup-ground-station") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    existing = [path for path in (station_path, remote_path, unit_path) if path.exists()]
    if existing:
        backup_dir.mkdir(parents=True)
        for path in existing:
            shutil.copy2(path, backup_dir / path.name)
        print("System configuration backup:", backup_dir, flush=True)
    station = json.loads((root / "config/station.example.json").read_text(encoding="utf8"))
    if station_path.exists():
        station = json.loads(station_path.read_text(encoding="utf8"))
    station["serial"]["screen"].update(port=args.screen_port, baudrate=9600)
    station["serial"]["fleet_radio"].update(port=args.radio_port, baudrate=115200)
    station_path.write_text(json.dumps(station, indent=2)+"\n", encoding="utf8")
    if not remote_path.exists():
        shutil.copy2(root / "config/competition_remote.example.json", remote_path)
    for path in (station_path, remote_path):
        path.chmod(0o644)
    subprocess.run(["/usr/bin/python3", "-B", "-m", "components.competition_remote",
                    "--station-config", str(station_path), "--remote-config", str(remote_path),
                    "--validate"], cwd=root, check=True)
    unit = (root / "deploy/competition-remote.service").read_text(encoding="utf8")
    unit = unit.replace("@PROJECT_ROOT@", '"'+str(root)+'"')
    unit = unit.replace("User=cooper", "User="+account.pw_name).replace("Group=cooper", "Group="+group)
    unit_path.write_text(unit, encoding="utf8")
    unit_path.chmod(0o644)
    subprocess.run(["systemd-analyze", "verify", str(unit_path)], check=True)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "competition-remote.service"], check=True)
    subprocess.run(["systemctl", "is-enabled", "competition-remote.service"], check=True)
    subprocess.run(["systemctl", "is-active", "competition-remote.service"], check=True)


if __name__ == "__main__":
    main()
