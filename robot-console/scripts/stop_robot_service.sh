#!/usr/bin/env bash
# Stop robot-service and attempt zero-velocity / estop via CLI when available.
set -euo pipefail

PID_FILE="${ROBOT_SERVICE_PID_FILE:-/tmp/singularity_go2_robot_service.pid}"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo "Stopping robot-service pid=$PID"
    kill "$PID" || true
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
else
  echo "No pid file at $PID_FILE"
fi

if command -v go2ctl >/dev/null 2>&1; then
  go2ctl emergency-stop 2>/dev/null || true
fi

echo "robot-service stop attempted"
