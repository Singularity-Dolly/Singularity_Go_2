#!/usr/bin/env bash
# Activate the existing DimOS/venv environment, then run go2ctl console.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  :
elif [[ -f "${ROOT}/dimos/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/dimos/.venv/bin/activate"
elif [[ -f "${ROOT}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/.venv/bin/activate"
fi
exec go2ctl console "$@"
