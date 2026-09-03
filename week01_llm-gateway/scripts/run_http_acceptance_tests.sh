#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
BLACKBOX_PORT="${BLACKBOX_PORT:-18000}"
BASE_URL="http://127.0.0.1:${BLACKBOX_PORT}"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/reports}"
BLACKBOX_DIR="$REPORT_DIR/blackbox"
BLACKBOX_DB_URL="${BLACKBOX_DB_URL:-$BLACKBOX_DIR/gateway.db}"
UVICORN_LOG="$BLACKBOX_DIR/uvicorn.log"
SUMMARY_FILE="$REPORT_DIR/blackbox-summary.md"

mkdir -p "$BLACKBOX_DIR"

UVICORN_PID=""
PASS_COUNT=0
FAIL_COUNT=0
FAILURES=""

CASE_OK=1
BODY_FILE=""
HEADERS_FILE=""
CODE=""

cleanup() {
  if [[ -n "$UVICORN_PID" ]] && kill -0 "$UVICORN_PID" 2>/dev/null; then
    kill -TERM "$UVICORN_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

begin_case() {
  local name="$1"
  CASE_OK=1
  BODY_FILE="$BLACKBOX_DIR/$name.body"
  HEADERS_FILE="$BLACKBOX_DIR/$name.headers"
  : > "$BODY_FILE"
  : > "$HEADERS_FILE"
}

run_request() {
  CODE="$(curl -sS -D "$HEADERS_FILE" -o "$BODY_FILE" -w '%{http_code}' "$@")"
}

fail_case() {
  CASE_OK=0
  echo "    FAIL: $*"
}

assert_status() {
  local expected="$1"
  [[ "$CODE" == "$expected" ]] || fail_case "status expected $expected, got $CODE"
}

assert_body_contains() {
  local needle="$1"
  grep -qF "$needle" "$BODY_FILE" || fail_case "body missing: $needle"
}

assert_header_contains() {
  local needle="$1"
  grep -qi "$needle" "$HEADERS_FILE" || fail_case "header missing: $needle"
}

assert_eq() {
  local actual="$1"
  local expected="$2"
  local message="$3"
  [[ "$actual" == "$expected" ]] || fail_case "$message expected $expected, got $actual"
}

finish_case() {
  local name="$1"
  local description="$2"

  if [[ "$CASE_OK" -eq 1 ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "PASS $name: $description"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILURES="$FAILURES $name"
    echo "FAIL $name: $description"
    echo "    body:"
    sed -n '1,80p' "$BODY_FILE"
    echo "    headers:"
    sed -n '1,80p' "$HEADERS_FILE"
  fi
}

echo "HTTP Blackbox Acceptance Pipeline"
echo "Root: $ROOT_DIR"
echo "Gateway: $BASE_URL"
echo "Report dir: $BLACKBOX_DIR"
echo "Timestamp: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
echo

BLACKBOX_DB_URL="$BLACKBOX_DB_URL" "$PYTHON_BIN" -m uvicorn \
  scripts.blackbox_app:create_blackbox_app \
  --factory \
  --host 127.0.0.1 \
  --port "$BLACKBOX_PORT" \
  >"$UVICORN_LOG" 2>&1 &
UVICORN_PID=$!

echo "Started Uvicorn with pid $UVICORN_PID"

READY=0
for _ in $(seq 1 60); do
  if curl -fsS "$BASE_URL/healthz" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 0.2
done

if [[ "$READY" -ne 1 ]]; then
  echo "Gateway did not become ready in time."
  echo "--- uvicorn log ---"
  sed -n '1,160p' "$UVICORN_LOG"
  exit 1
fi

echo

begin_case bh01
run_request "$BASE_URL/healthz"
assert_status 200
assert_body_contains '"status":"ok"'
finish_case "BH-01" "GET /healthz returns 200 without auth"

begin_case bh02
run_request "$BASE_URL/readyz"
assert_status 200
assert_body_contains '"status":"ok"'
finish_case "BH-02" "GET /readyz returns 200 without auth"

begin_case bh03
run_request "$BASE_URL/v1/models"
assert_status 401
assert_body_contains 'invalid_api_key'
finish_case "BH-03" "GET /v1/models without token returns 401"

begin_case bh04
run_request \
  -H "Authorization: Bearer test-key" \
  "$BASE_URL/v1/models"
assert_status 200
assert_body_contains '"object":"list"'
assert_body_contains '"smart"'
assert_body_contains '"claude-fast"'
finish_case "BH-04" "GET /v1/models with token returns model aliases"

begin_case bh05
run_request \
  -X POST "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer test-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"hello"}]}'
assert_status 200
assert_body_contains '"object":"chat.completion"'
assert_body_contains 'blackbox-ok'
assert_header_contains '^x-request-id:'
finish_case "BH-05" "non-stream chat completion returns native response and request id"

begin_case bh06
run_request \
  -N \
  -X POST "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer test-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","stream":true,"messages":[{"role":"user","content":"hello"}]}'
assert_status 200
assert_header_contains '^content-type: text/event-stream'
assert_header_contains '^x-request-id:'
assert_header_contains '^cache-control: no-cache, no-transform'
assert_header_contains '^x-accel-buffering: no'
assert_body_contains 'data: [DONE]'
finish_case "BH-06" "SSE chat completion streams over HTTP"

begin_case bh07
run_request \
  -X POST "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer rate-limit-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"rate-limit-1"}]}'
FIRST_RATE_CODE="$CODE"
run_request \
  -X POST "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer rate-limit-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"rate-limit-2"}]}'
SECOND_RATE_CODE="$CODE"
run_request \
  -X POST "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer rate-limit-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"rate-limit-3"}]}'
assert_eq "$FIRST_RATE_CODE" 200 "first rate-limit request"
assert_eq "$SECOND_RATE_CODE" 200 "second rate-limit request"
assert_status 429
assert_header_contains '^retry-after:'
assert_body_contains 'rate_limit_exceeded'
finish_case "BH-07" "rate limit returns 429 and Retry-After"

echo
echo "Stopping Uvicorn gracefully for BH-08"
kill -TERM "$UVICORN_PID" 2>/dev/null || true

UVICORN_EXIT=1
for _ in $(seq 1 60); do
  if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
    break
  fi
  sleep 0.2
done

if kill -0 "$UVICORN_PID" 2>/dev/null; then
  kill -KILL "$UVICORN_PID" 2>/dev/null || true
fi

wait "$UVICORN_PID" 2>/dev/null
UVICORN_PID=""

begin_case bh08
if ! grep -qF "Shutting down" "$UVICORN_LOG"; then
  fail_case "uvicorn log missing Shutting down"
fi
if ! grep -qF "Application shutdown complete" "$UVICORN_LOG"; then
  fail_case "uvicorn log missing Application shutdown complete"
fi
finish_case "BH-08" "service exits gracefully on SIGTERM"

echo
TOTAL=$((PASS_COUNT + FAIL_COUNT))
{
  echo "# HTTP Blackbox Acceptance Summary"
  echo
  echo "- Total: $TOTAL"
  echo "- Passed: $PASS_COUNT"
  echo "- Failed: $FAIL_COUNT"
  echo "- Gateway: $BASE_URL"
  echo "- Timestamp: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  echo
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    echo "## Result"
    echo
    echo "All HTTP blackbox smoke cases passed."
  else
    echo "## Failed Cases"
    echo
    for name in $FAILURES; do
      echo "- $name"
    done
  fi
} > "$SUMMARY_FILE"

cat "$SUMMARY_FILE"
echo

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  echo "HTTP blackbox acceptance passed."
else
  echo "HTTP blackbox acceptance failed."
fi

exit "$FAIL_COUNT"
