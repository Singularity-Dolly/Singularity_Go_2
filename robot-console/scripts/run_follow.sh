#!/usr/bin/env bash
# Physical front-person follow: YOLO (+ EdgeTAM if CUDA) + sport Move.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if [[ -f "${ROOT}/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${ROOT}/.venv/bin/activate"
  elif [[ -f "${ROOT}/dimos/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${ROOT}/dimos/.venv/bin/activate"
  fi
fi

# Prefer local weights (avoids Ultralytics re-download).
if [[ -z "${GO2CTL_DETECTOR_MODEL:-}" ]]; then
  if [[ -f "${ROOT}/robot-console/models/yolov8n.pt" ]]; then
    export GO2CTL_DETECTOR_MODEL="${ROOT}/robot-console/models/yolov8n.pt"
  elif [[ -f "${ROOT}/weights/yolov8n.pt" ]]; then
    export GO2CTL_DETECTOR_MODEL="${ROOT}/weights/yolov8n.pt"
  fi
fi

CONNECTION_MODE="${GO2CTL_CONNECTION_MODE:-ap}"
ARGS=(
  follow
  --connection-mode "${CONNECTION_MODE}"
  --allow-normal-mode-switch
)
if [[ -n "${GO2CTL_AES_KEY_FILE:-}" ]]; then
  ARGS+=(--aes-key-file "${GO2CTL_AES_KEY_FILE}")
elif [[ -f "${HOME}/.config/go2ctl/aes_key" ]]; then
  ARGS+=(--aes-key-file "${HOME}/.config/go2ctl/aes_key")
fi
if [[ -n "${ROBOT_IP:-}" ]]; then
  ARGS+=(--robot-ip "${ROBOT_IP}")
fi
echo "Starting Go2 smooth face-aim follow…"
echo "Detector: ${GO2CTL_DETECTOR_MODEL:-yolov8n.pt (default lookup)}"
echo "Stand ~1.5m in front. Soft follow — stop and robot stops."
echo "SPACE=estop  ESC=quit  X=hold"
exec go2ctl "${ARGS[@]}" "$@"
