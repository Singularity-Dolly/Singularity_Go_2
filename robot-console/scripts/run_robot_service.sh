#!/usr/bin/env bash
# Launch authenticated thin robot-service around Go2Controller.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${ROBOT_SERVICE_HOST:-${GATEWAY_HOST:-0.0.0.0}}"
PORT="${ROBOT_SERVICE_PORT:-${GATEWAY_PORT:-8780}}"
PID_FILE="${ROBOT_SERVICE_PID_FILE:-/tmp/singularity_go2_robot_service.pid}"

if [[ -z "${ROBOT_AUTH_TOKEN:-}" && -z "${GATEWAY_AUTH_TOKEN:-}" ]]; then
  echo "ROBOT_AUTH_TOKEN is required (never commit the real token)." >&2
  exit 1
fi

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "robot-service already running pid=$(cat "$PID_FILE")" >&2
  exit 1
fi

export GO2CTL_MOTION_ENABLED="${GO2CTL_MOTION_ENABLED:-false}"
export GO2CTL_CONNECTION_MODE="${GO2CTL_CONNECTION_MODE:-${CONNECTION_MODE:-ap}}"

echo "Starting robot-service on ${HOST}:${PORT} (motion=${GO2CTL_MOTION_ENABLED})"
python3 -m singularity_go2_console.service.app &
echo $! >"$PID_FILE"
echo "pid=$(cat "$PID_FILE")"
echo "Logs: attach to this terminal process; stop with scripts/stop_robot_service.sh"
wait
