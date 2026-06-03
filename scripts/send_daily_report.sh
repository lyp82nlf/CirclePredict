#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"

source "$SCRIPT_DIR/resolve_python.sh"

cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -m circle_predict.daily_report "$@"
