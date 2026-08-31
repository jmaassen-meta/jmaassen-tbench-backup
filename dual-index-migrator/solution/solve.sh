#!/bin/bash
set -e
APP_DIR=${APP_DIR:-/app}
mkdir -p "$APP_DIR/dual_index"
cp -r "$(dirname "$0")/files/dual_index/." "$APP_DIR/dual_index/"
echo "Solution copied to $APP_DIR/dual_index"
