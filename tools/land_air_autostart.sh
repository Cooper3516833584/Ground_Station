#!/usr/bin/env bash
# Desktop-session launcher for the D-task read-only FleetBus display.

set -eu

readonly APP_DIR=/home/cooper/Desktop/Ground_Station
readonly LOG_DIR=/home/cooper/.cache
readonly LOG_FILE="${LOG_DIR}/ground-station-land-air.log"

mkdir -p "${LOG_DIR}"
exec /usr/bin/python3 "${APP_DIR}/land_air_app.py" >>"${LOG_FILE}" 2>&1
