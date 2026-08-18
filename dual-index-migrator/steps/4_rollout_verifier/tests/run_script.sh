#!/bin/bash
set -e

# The gold test files are held out at /tests (Harbor copies this dir there at
# verify time); the agent's working tree is /app. We invoke pytest from `/` so
# node-ids print as `tests/<file>::<test>` (parser.py's regex expects the
# `tests/` prefix), and put /app on PYTHONPATH so `from src.calc import run`
# resolves against the code the agent wrote at /app/src/calc.py.
export PYTHONPATH="/app${PYTHONPATH:+:$PYTHONPATH}"

run_all_tests() {
  echo "Running all tests..."
  cd /
  python -m pytest tests -v --tb=short || true
}

run_selected_tests() {
  local test_files=("$@")
  echo "Running selected tests: ${test_files[*]}"
  cd /

  # Strip pytest node-ids (tests/x.py::a -> tests/x.py) and de-duplicate, so a
  # file with several selected node-ids runs once, not once per node-id.
  local -A seen=()
  local test_file test_path
  for test_file in "${test_files[@]}"; do
    test_path="${test_file%%::*}"
    if [ -n "${seen[$test_path]:-}" ]; then
      continue
    fi
    seen[$test_path]=1
    echo "Running test file: $test_path"
    python -m pytest "$test_path" -v --tb=short || true
  done
}

if [ $# -eq 0 ]; then
  run_all_tests
  exit $?
fi

if [[ "$1" == *","* ]]; then
  IFS=',' read -r -a TEST_FILES <<< "$1"
else
  TEST_FILES=("$@")
fi

run_selected_tests "${TEST_FILES[@]}"
