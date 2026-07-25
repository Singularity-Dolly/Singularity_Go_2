#!/usr/bin/env bash
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
ARGS=(--mode manual --connection-mode "${CONNECTION_MODE}")
if [[ -n "${GO2CTL_AES_KEY_FILE:-}" ]]; then
  ARGS+=(--aes-key-file "${GO2CTL_AES_KEY_FILE}")
elif [[ -f "${HOME}/.config/go2ctl/aes_key" ]]; then
  ARGS+=(--aes-key-file "${HOME}/.config/go2ctl/aes_key")
fi
if [[ -n "${ROBOT_IP:-}" ]]; then
  ARGS+=(--robot-ip "${ROBOT_IP}")
fi
exec go2ctl console "${ARGS[@]}" "$@"
