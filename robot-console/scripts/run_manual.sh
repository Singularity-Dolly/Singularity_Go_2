#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if [[ -f "${ROOT}/dimos/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${ROOT}/dimos/.venv/bin/activate"
  elif [[ -f "${ROOT}/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${ROOT}/.venv/bin/activate"
  fi
fi
exec go2ctl console --mode manual "$@"
