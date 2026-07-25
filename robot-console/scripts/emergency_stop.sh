#!/usr/bin/env bash
set -euo pipefail
echo "Emergency stop: if go2ctl console is running, press SPACE."
echo "Also use the official Unitree remote / physical recovery method."
if command -v go2ctl >/dev/null 2>&1; then
  go2ctl estop "$@"
fi
