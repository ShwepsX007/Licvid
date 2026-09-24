#!/usr/bin/env bash
# Прогон тестов LiqScope.
#
#   bash tools/run_tests.sh              # python; js — если поднят стенд
#   bash tools/run_tests.sh --python     # только python
#   LIQSCOPE_TEST_URL=http://127.0.0.1:8000 bash tools/run_tests.sh
#
# jsdom-тесты глушат gtag через tests/_dom_env.js (NODE_OPTIONS).
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif [[ -x /tmp/liqvenv/bin/python ]]; then
  PY=/tmp/liqvenv/bin/python
fi

ONLY_PY=0
if [[ "${1:-}" == "--python" ]]; then
  ONLY_PY=1
fi

export LIQSCOPE_SECRET="${LIQSCOPE_SECRET:-test-secret-not-the-published-default}"
export NODE_OPTIONS="${NODE_OPTIONS:-} --require $ROOT/tests/_dom_env.js"

fail=0
echo "== python =="
for f in tests/test_*.py; do
  if ! "$PY" "$f" >/tmp/liq-test.out 2>&1; then
    echo "FAIL $f"
    tail -n 30 /tmp/liq-test.out
    fail=1
  else
    echo "ok   $f"
  fi
done

if [[ "$ONLY_PY" == 1 ]]; then
  exit "$fail"
fi

URL="${LIQSCOPE_TEST_URL:-}"
if [[ -z "$URL" ]]; then
  echo "== js: пропуск (нет LIQSCOPE_TEST_URL) =="
  exit "$fail"
fi

echo "== js ($URL) =="
export NODE_PATH="${NODE_PATH:-$ROOT/node_modules}"
for f in tests/*.js; do
  base="$(basename "$f")"
  case "$base" in
    _*) continue ;;
  esac
  if ! node "$f" "$URL" >/tmp/liq-test.out 2>&1; then
    echo "FAIL $f"
    tail -n 20 /tmp/liq-test.out
    fail=1
  else
    echo "ok   $f"
  fi
done

exit "$fail"
