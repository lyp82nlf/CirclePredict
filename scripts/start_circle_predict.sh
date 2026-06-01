#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  source "$PROJECT_DIR/.env"
  set +a
fi

export CIRCLEPREDICT_HOST="${CIRCLEPREDICT_HOST:-0.0.0.0}"
export CIRCLEPREDICT_PORT="${CIRCLEPREDICT_PORT:-15121}"
export CIRCLEPREDICT_PROXY_URL="${CIRCLEPREDICT_PROXY_URL:-http://127.0.0.1:7890}"

cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -m circle_predict.server
