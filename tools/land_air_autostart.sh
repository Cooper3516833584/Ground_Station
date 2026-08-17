#!/usr/bin/env bash
# Desktop-session launcher for the D-task read-only FleetBus display.
# Computes the project root from its own location, so it works from any
# checkout path and any user home.

set -eu

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${HOME}/.cache"
LOG_FILE="${LOG_DIR}/ground-station-land-air.log"

mkdir -p "${LOG_DIR}"
exec /usr/bin/python3 "${APP_DIR}/land_air_app.py" >>"${LOG_FILE}" 2>&1
