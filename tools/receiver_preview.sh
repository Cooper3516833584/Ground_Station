#!/usr/bin/env bash
# Temporary kiosk preview for the ground-station USB video receiver.
# It waits for the UVC capture node, displays it full-screen, and reconnects
# automatically whenever the receiver or its video signal is interrupted.

set -u -o pipefail

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export XDG_RUNTIME_DIR="$RUNTIME_DIR"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-wayland}"

LOG_FILE="$HOME/.cache/ground-station-receiver-preview.log"
PID_FILE="$RUNTIME_DIR/ground-station-receiver-preview.pid"
mkdir -p "$(dirname "$LOG_FILE")"

child_pid=""
cleanup() {
    if [[ -n "$child_pid" ]]; then
        kill "$child_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
}
trap cleanup EXIT INT TERM
echo "$$" > "$PID_FILE"

find_receiver() {
    local device details
    # UVC devices expose index0 as the actual image stream and index1 as
    # metadata.  Select through the persistent by-id link so the receiver is
    # never confused with its metadata node or a Pi codec node.
    for device in /dev/v4l/by-id/*video-index0; do
        [[ -e "$device" ]] || continue
        details="$(v4l2-ctl --device "$device" --all 2>/dev/null || true)"
        if grep -q "Driver name *: uvcvideo" <<<"$details"; then
            printf '%s\n' "$device"
            return 0
        fi
    done
    return 1
}

while true; do
    receiver="$(find_receiver || true)"
    if [[ -z "$receiver" ]]; then
        sleep 1
        continue
    fi

    printf '%s opening %s\n' "$(date --iso-8601=seconds)" "$receiver" >> "$LOG_FILE"
    ffplay -fs -noborder -an -loglevel warning -fflags nobuffer -flags low_delay \
        -framedrop -f video4linux2 "$receiver" >> "$LOG_FILE" 2>&1 &
    child_pid="$!"
    # ffplay can stay alive after a USB receiver vanishes; explicitly release
    # it so the next loop can claim the newly-created V4L2 node.
    while kill -0 "$child_pid" 2>/dev/null; do
        if [[ ! -e "$receiver" ]]; then
            kill "$child_pid" 2>/dev/null || true
            break
        fi
        sleep 0.5
    done
    wait "$child_pid" || true
    child_pid=""
    sleep 1
done
