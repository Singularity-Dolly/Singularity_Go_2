#!/usr/bin/env bash
# Physical front-person follow: YOLO + EdgeTAM + sport Move.
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
echo "Starting Go2 front-person follow…"
echo "Stand in front of the camera. SPACE=estop  ESC=quit  X=hold"
exec go2ctl "${ARGS[@]}" "$@"
