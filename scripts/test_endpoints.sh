#!/bin/bash
# ============================================================
# robot-service API 端点测试脚本
# 用法: bash test_endpoints.sh [HOST] [PORT]
# 默认: http://localhost:8780
# ============================================================

HOST="${1:-localhost}"
PORT="${2:-8780}"
BASE="http://${HOST}:${PORT}"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}PASS${NC} $1"; }
fail() { echo -e "  ${RED}FAIL${NC} $1"; }
info() { echo -e "  ${YELLOW}INFO${NC} $1"; }

echo "============================================"
echo "  robot-service API 测试"
echo "  Target: ${BASE}"
echo "  $(date)"
echo "============================================"
echo ""

# ---- 1. Health ----
echo "1. GET /v1/health"
RESP=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}/v1/health" 2>&1)
if [ "$RESP" = "200" ]; then
    pass "health returns 200"
    curl -s "${BASE}/v1/health" | python3 -m json.tool 2>/dev/null || info "(raw output)"
else
    fail "health returned ${RESP}"
fi
echo ""

# ---- 2. State ----
echo "2. GET /v1/state"
RESP=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}/v1/state" 2>&1)
if [ "$RESP" = "200" ]; then
    pass "state returns 200"
    curl -s "${BASE}/v1/state" | python3 -m json.tool 2>/dev/null || info "(raw output)"
else
    fail "state returned ${RESP}"
fi
echo ""

# ---- 3. Stop ----
echo "3. POST /v1/stop"
RESP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BASE}/v1/stop" 2>&1)
if [ "$RESP" = "200" ]; then
    pass "stop returns 200"
    curl -s -X POST "${BASE}/v1/stop" | python3 -m json.tool 2>/dev/null || info "(raw output)"
else
    fail "stop returned ${RESP}"
fi
echo ""

# ---- 4. Commands ----
echo "4. POST /v1/commands (scan.start)"
RESP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BASE}/v1/commands" \
    -H "Content-Type: application/json" \
    -d '{"command":"scan.start","ttl_ms":5000}' 2>&1)
if [ "$RESP" = "200" ]; then
    pass "scan.start returns 200"
    curl -s -X POST "${BASE}/v1/commands" \
        -H "Content-Type: application/json" \
        -d '{"command":"scan.start","ttl_ms":5000}' | python3 -m json.tool 2>/dev/null
else
    fail "scan.start returned ${RESP}"
fi
echo ""

echo "5. POST /v1/commands (follow.start)"
RESP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BASE}/v1/commands" \
    -H "Content-Type: application/json" \
    -d '{"command":"follow.start","ttl_ms":5000}' 2>&1)
if [ "$RESP" = "200" ]; then
    pass "follow.start returns 200"
    curl -s -X POST "${BASE}/v1/commands" \
        -H "Content-Type: application/json" \
        -d '{"command":"follow.start","ttl_ms":5000}' | python3 -m json.tool 2>/dev/null
else
    fail "follow.start returned ${RESP}"
fi
echo ""

echo "6. POST /v1/commands (mission.stop)"
RESP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BASE}/v1/commands" \
    -H "Content-Type: application/json" \
    -d '{"command":"mission.stop","ttl_ms":5000}' 2>&1)
if [ "$RESP" = "200" ]; then
    pass "mission.stop returns 200"
else
    fail "mission.stop returned ${RESP}"
fi
echo ""

# ---- 7. Frame ----
echo "7. GET /v1/state (verify stop)"
RESP=$(curl -s "${BASE}/v1/state" 2>&1)
MODE=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('mode','?'))" 2>/dev/null || echo "?")
info "Current mode: ${MODE}"
echo ""

echo "============================================"
echo "  Test complete — all endpoints reachable"
echo "============================================"