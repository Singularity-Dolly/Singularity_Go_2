#!/usr/bin/env bash
# Minimal WASD Go2 walker (AP + AES).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
exec python "$ROOT/robot-console/scripts/go2_wasd.py" "$@"
