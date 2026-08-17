#!/usr/bin/env bash
# One-time setup for the Ground Station on a new Raspberry Pi.
#
# This script:
#   1. checks for Python 3;
#   2. creates config/station.local.json from the example if it is missing
#      (it never overwrites an existing station.local.json);
#   3. checks the Python dependencies (and offers to install them);
#   4. installs the LED daemon systemd unit using THIS checkout path;
#   5. optionally installs the land-air desktop autostart entry.
#
# It never generates or writes an HMAC key.  Nothing is modified without a
# clear prompt first.

set -eu

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

echo "==> Ground Station setup"
echo "    project directory: ${APP_DIR}"

# ---------------------------------------------------------------------------
# 1. Python check
# ---------------------------------------------------------------------------
PYTHON="${PYTHON:-python3}"
if ! command -v "${PYTHON}" >/dev/null 2>&1; then
    echo "ERROR: ${PYTHON} not found. Install Python 3 first (e.g. sudo apt install python3)." >&2
    exit 1
fi
echo "==> Python: $(${PYTHON} --version 2>&1)"

# ---------------------------------------------------------------------------
# 2. Station configuration
# ---------------------------------------------------------------------------
CONFIG_DIR="${APP_DIR}/config"
LOCAL_CONFIG="${CONFIG_DIR}/station.local.json"
EXAMPLE_CONFIG="${CONFIG_DIR}/station.example.json"
if [[ ! -f "${EXAMPLE_CONFIG}" ]]; then
    echo "ERROR: ${EXAMPLE_CONFIG} is missing; cannot create the local config." >&2
    exit 1
fi
if [[ ! -f "${LOCAL_CONFIG}" ]]; then
    cp "${EXAMPLE_CONFIG}" "${LOCAL_CONFIG}"
    echo "==> Created ${LOCAL_CONFIG} from the example."
    echo "    IMPORTANT: edit it before running the station:"
    echo "      - serial.screen.port        -> your screen USB serial device"
    echo "      - serial.fleet_radio.port   -> your HC-14 USB serial device"
    echo "      - hardware.led.pin/count    -> your WS2812 GPIO and LED count"
    echo "      - hardware.buzzer.pin       -> your buzzer GPIO (or enabled:false)"
else
    echo "==> ${LOCAL_CONFIG} already exists; leaving it untouched."
fi

# ---------------------------------------------------------------------------
# 3. Python dependencies
# ---------------------------------------------------------------------------
REQUIREMENTS="${APP_DIR}/requirements.txt"
MISSING=()
for module in serial PyQt5; do
    if ! "${PYTHON}" -c "import ${module}" >/dev/null 2>&1; then
        MISSING+=("${module}")
    fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "==> Missing Python modules: ${MISSING[*]}"
    echo "    Install them with:"
    echo "        ${PYTHON} -m pip install -r ${REQUIREMENTS}"
    read -r -p "    Run this now? [y/N] " answer
    if [[ "${answer}" =~ ^[Yy]$ ]]; then
        "${PYTHON}" -m pip install -r "${REQUIREMENTS}"
    fi
else
    echo "==> Required Python modules are available."
fi
# Raspberry-Pi-only hardware modules are checked separately because they are
# not available on a normal desktop.
for module in RPi rpi_ws281x; do
    if ! "${PYTHON}" -c "import ${module}" >/dev/null 2>&1; then
        echo "    NOTE: '${module}' is not importable (expected on a non-Pi machine;"
        echo "          needed for the buzzer/LED daemon on the Raspberry Pi)."
    fi
done

# ---------------------------------------------------------------------------
# 4. LED daemon systemd unit
# ---------------------------------------------------------------------------
SERVICE_TEMPLATE="${SCRIPT_DIR}/ground-station-led.service"
UNIT_NAME="ground-station-led.service"
echo "==> Installing the LED daemon systemd unit (${UNIT_NAME})"
echo "    (runs '${PYTHON} ${APP_DIR}/led_daemon.py' as the sole WS2812 owner)"
read -r -p "    Install and enable now? [y/N] " answer
if [[ "${answer}" =~ ^[Yy]$ ]]; then
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "    sudo is needed to write /etc/systemd/system/." >&2
        if ! command -v sudo >/dev/null 2>&1; then
            echo "ERROR: sudo not available." >&2
            exit 1
        fi
    fi
    tmp_unit="$(mktemp)"
    sed -e "s|@APP_DIR@|${APP_DIR}|g" -e "s|@PYTHON@|$(command -v "${PYTHON}")|g" \
        "${SERVICE_TEMPLATE}" > "${tmp_unit}"
    if [[ "$(id -u)" -eq 0 ]]; then
        install -m 644 "${tmp_unit}" "/etc/systemd/system/${UNIT_NAME}"
    else
        sudo install -m 644 "${tmp_unit}" "/etc/systemd/system/${UNIT_NAME}"
    fi
    rm -f "${tmp_unit}"
    if [[ "$(id -u)" -eq 0 ]]; then
        systemctl daemon-reload
        systemctl enable --now "${UNIT_NAME}"
    else
        sudo systemctl daemon-reload
        sudo systemctl enable --now "${UNIT_NAME}"
    fi
    echo "    Started ${UNIT_NAME}. Check with: systemctl status ${UNIT_NAME}"
else
    echo "    Skipped. You can install it later by running ${SERVICE_TEMPLATE} manually."
fi

# ---------------------------------------------------------------------------
# 5. Land-air desktop autostart (optional)
# ---------------------------------------------------------------------------
DESKTOP_TEMPLATE="${SCRIPT_DIR}/ground-station-land-air.desktop.in"
AUTOSTART_DIR="${HOME}/.config/autostart"
echo "==> Optional desktop autostart for the D-task display"
read -r -p "    Install to ${AUTOSTART_DIR}? [y/N] " answer
if [[ "${answer}" =~ ^[Yy]$ ]]; then
    mkdir -p "${AUTOSTART_DIR}"
    sed -e "s|@APP_DIR@|${APP_DIR}|g" \
        "${DESKTOP_TEMPLATE}" > "${AUTOSTART_DIR}/ground-station-land-air.desktop"
    echo "    Installed ${AUTOSTART_DIR}/ground-station-land-air.desktop"
else
    echo "    Skipped. Copy ${DESKTOP_TEMPLATE} and replace @APP_DIR@ manually if needed."
fi

# ---------------------------------------------------------------------------
# 6. HMAC key reminder
# ---------------------------------------------------------------------------
echo "==> HMAC key"
echo "    The station requires an HMAC key. It is NEVER generated by this script"
echo "    and never stored in JSON. Create it once with:"
echo "        python3 -c \"import secrets; print(secrets.token_hex(32))\" \\"
echo "            > config/secrets/hmac.key"
echo "    or export GROUND_STATION_HMAC_KEY_HEX in the session that runs the apps."
echo "==> Done. Next: nano ${LOCAL_CONFIG}, then 'python3 ${APP_DIR}/main.py'"
