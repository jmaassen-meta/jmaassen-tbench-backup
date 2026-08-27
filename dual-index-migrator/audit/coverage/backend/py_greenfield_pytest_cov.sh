#!/usr/bin/env bash
# status: validated on dual-index-migrator (host-local draft, 2026-08-27, python3.9, 76.4% whole-solution with subprocess hook)
# Python greenfield pytest black-box — covers CLI subprocesses via COVERAGE_PROCESS_START
set -uo pipefail
COVDIR="${COVDIR:-/tmp/cov_out}"
RC="$COVDIR/coveragerc"
mkdir -p "$COVDIR"
rm -f "$COVDIR"/.coverage* "$COVDIR"/coverage.json "$COVDIR"/coverage.lcov 2>/dev/null || true
export COVERAGE_FILE="$COVDIR/.coverage"
PY="python3.9"
cat > "$RC" <<EOF
[run]
branch = True
parallel = True
sigterm = True
source = /tmp/app_test_cov
omit =
    */site-packages/*
    */dist-packages/*
    */__pycache__/*
    /tmp/cov_out/*
    */tests/*
EOF
# hook must be in user site where coverage is installed (/home/jmaassen/.local/...)
SITE_USER="$($PY -c 'import site; print(site.getusersitepackages())')"
echo 'import coverage; coverage.process_startup()' > "$SITE_USER/zz_cov_subprocess.pth"
export COVERAGE_PROCESS_START="$RC"
echo "[cov] hook at $SITE_USER/zz_cov_subprocess.pth ; COVERAGE_PROCESS_START=$RC"
[ -d /tmp/app_test_cov/dual_index ] || { echo "[cov] FATAL: /tmp/app_test_cov missing"; exit 4; }
echo "[cov] running grader with subprocess hook"
PYTHONPATH=/tmp/app_test_cov COVERAGE_PROCESS_START="$RC" COVERAGE_FILE="$COVDIR/.coverage" $PY -m coverage run --rcfile="$RC" -m pytest /home/jmaassen/jmaassen-tbench/dual-index-migrator/steps/ -q 2>&1 | tail -n 20
echo "[cov] data files:"; ls -lh "$COVDIR"/.coverage* 2>&1 | head -n 40
echo "[cov] combining"
$PY -m coverage combine --rcfile="$RC" 2>&1 | tail -n 10
echo "[cov] === report ==="
$PY -m coverage report --rcfile="$RC" -m 2>&1 | tail -n 40
$PY -m coverage json --rcfile="$RC" -o "$COVDIR/coverage.json" 2>&1 | tail -n 5 && echo "[cov] wrote $COVDIR/coverage.json"
$PY -m coverage lcov --rcfile="$RC" -o "$COVDIR/coverage.lcov" 2>&1 | tail -n 5 && echo "[cov] wrote $COVDIR/coverage.lcov"
