#!/usr/bin/env bash
# No-motion robot preflight for Singularity Go2 console / robot-service.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${CONNECTION_MODE:-${GO2CTL_CONNECTION_MODE:-ap}}"
ROBOT_IP="${ROBOT_IP:-}"
AES_KEY_FILE="${GO2CTL_AES_KEY_FILE:-}"
MOTION_GUARD=1

echo "=== Singularity Go2 no-motion preflight ==="
echo "connection_mode=$MODE"
echo "NOTE: This script must never send non-zero velocity."

PASS=0
FAIL=0
row() {
  local name="$1" status="$2" detail="$3"
  printf "%-28s %-6s %s\n" "$name" "$status" "$detail"
  if [[ "$status" == "PASS" ]]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
  fi
}

python3 - <<'PY' || true
import sys
print(sys.version.split()[0])
PY

if python3 -c "import singularity_go2_console" 2>/dev/null; then
  row "package_import" "PASS" "singularity_go2_console"
else
  row "package_import" "FAIL" "pip install -e . in robot-console"
fi

# AES presence (never print the key)
if [[ -n "${UNITREE_AES_128_KEY:-}" ]]; then
  row "aes_env" "PASS" "UNITREE_AES_128_KEY set"
elif [[ -n "$AES_KEY_FILE" && -f "$AES_KEY_FILE" ]]; then
  row "aes_file" "PASS" "key file present"
elif [[ -f "${HOME}/.config/go2ctl/aes_key" ]]; then
  row "aes_config" "PASS" "~/.config/go2ctl/aes_key present"
else
  row "aes_key" "FAIL" "AES_KEY_REQUIRED"
fi

if [[ "$MODE" == "ap" ]]; then
  ROBOT_IP="${ROBOT_IP:-192.168.12.1}"
  row "mode_ap" "PASS" "LocalAP target $ROBOT_IP"
elif [[ "$MODE" == "sta" ]]; then
  if [[ -z "$ROBOT_IP" ]]; then
    row "mode_sta" "FAIL" "ROBOT_IP required for STA"
  else
    row "mode_sta" "PASS" "LocalSTA target $ROBOT_IP"
  fi
else
  row "mode" "FAIL" "connection mode must be ap|sta"
fi

# go2ctl preflight when available — still no-motion
if command -v go2ctl >/dev/null 2>&1 && [[ -n "$ROBOT_IP" ]]; then
  set +e
  OUT="$(go2ctl preflight --robot-ip "$ROBOT_IP" --connection-mode "$MODE" ${AES_KEY_FILE:+--aes-key-file "$AES_KEY_FILE"} 2>&1)"
  RC=$?
  set -e
  if [[ $RC -eq 0 ]]; then
    row "go2ctl_preflight" "PASS" "exit 0"
  else
    row "go2ctl_preflight" "FAIL" "exit $RC"
  fi
  # Guard: output must not claim non-zero motion
  if echo "$OUT" | grep -qiE 'vx=[1-9]|non-zero motion|moved'; then
    row "zero_velocity_guard" "FAIL" "preflight output suggests motion"
    MOTION_GUARD=0
  else
    row "zero_velocity_guard" "PASS" "no non-zero motion claimed"
  fi
else
  row "go2ctl_preflight" "FAIL" "go2ctl unavailable or ROBOT_IP unset"
fi

echo "PASS=$PASS FAIL=$FAIL"
if [[ "$FAIL" -gt 0 || "$MOTION_GUARD" -eq 0 ]]; then
  exit 1
fi
exit 0
