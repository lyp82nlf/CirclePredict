#!/bin/zsh
set -euo pipefail

LABEL="com.circlepredict.server"
REPORT_LABEL="com.circlepredict.daily-report"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
REPORT_PLIST_PATH="$HOME/Library/LaunchAgents/${REPORT_LABEL}.plist"

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$REPORT_PLIST_PATH" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"
rm -f "$REPORT_PLIST_PATH"

echo "CirclePredict LaunchAgents removed."
