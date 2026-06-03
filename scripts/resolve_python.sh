if [[ -n "${PYTHON_BIN:-}" ]]; then
  return 0
fi

if [[ -x "$PROJECT_DIR/.venv/bin/python3" ]]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python3"
elif [[ -x "$PROJECT_DIR/venv/bin/python3" ]]; then
  PYTHON_BIN="$PROJECT_DIR/venv/bin/python3"
else
  PYTHON_BIN="python3"
fi

export PYTHON_BIN
