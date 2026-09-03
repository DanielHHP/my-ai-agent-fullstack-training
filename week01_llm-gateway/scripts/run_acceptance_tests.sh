#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/reports}"
mkdir -p "$REPORT_DIR"

echo "Acceptance Test Pipeline"
echo "Root: $ROOT_DIR"
echo "Python: $PYTHON_BIN"
echo "Report dir: $REPORT_DIR"
echo "Timestamp: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
echo "Git branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
echo "Git commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo

{
  echo "# Acceptance Test Environment"
  echo
  echo "Timestamp: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  echo "Root: $ROOT_DIR"
  echo "Python: $("$PYTHON_BIN" --version 2>&1)"
  echo "pytest: $("$PYTHON_BIN" -m pytest --version 2>&1 | head -n 1)"
  echo "ruff: $("$PYTHON_BIN" -m ruff --version 2>&1 | head -n 1)"
  echo "Git branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  echo "Git commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
} | tee "$REPORT_DIR/environment.txt"
echo

PIPELINE_FAILED=0

if ! "$PYTHON_BIN" -m ruff check .; then
  PIPELINE_FAILED=1
fi
echo

if ! "$PYTHON_BIN" -m pytest \
    --junitxml="$REPORT_DIR/junit.xml" \
    --tb=short \
    -rA \
    --durations=20 \
    -vv \
    tests; then
  PIPELINE_FAILED=1
fi
echo

"$PYTHON_BIN" "$ROOT_DIR/scripts/summarize_junit.py" "$REPORT_DIR/junit.xml" || true
echo

if [[ "$PIPELINE_FAILED" -ne 0 ]]; then
  echo "Acceptance pipeline failed."
else
  echo "Acceptance pipeline passed."
fi

exit "$PIPELINE_FAILED"
