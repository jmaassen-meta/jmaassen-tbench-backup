#!/bin/bash
set -euo pipefail
APP_DIR="${APP_DIR:-/app}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$APP_DIR/dual_index"
cp -r "$SCRIPT_DIR/files/dual_index/." "$APP_DIR/dual_index/"
echo "solve.sh: installed dual_index (with per-universe atomic) to $APP_DIR"
