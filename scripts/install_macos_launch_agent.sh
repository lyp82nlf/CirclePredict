#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
LABEL="com.circlepredict.server"
REPORT_LABEL="com.circlepredict.daily-report"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
REPORT_PLIST_PATH="$HOME/Library/LaunchAgents/${REPORT_LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/CirclePredict"

if [[ ! -f "$PROJECT_DIR/.env" && -f "$PROJECT_DIR/.env.example" ]]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>${PROJECT_DIR}/scripts/start_circle_predict.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/server.out.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/server.err.log</string>
</dict>
</plist>
PLIST

cat > "$REPORT_PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${REPORT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>${PROJECT_DIR}/scripts/send_daily_report.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_DIR}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>10</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/daily-report.out.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/daily-report.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/${LABEL}"
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

launchctl bootout "gui/$(id -u)" "$REPORT_PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$REPORT_PLIST_PATH"
launchctl enable "gui/$(id -u)/${REPORT_LABEL}"

echo "CirclePredict LaunchAgent installed: $PLIST_PATH"
echo "CirclePredict daily report LaunchAgent installed: $REPORT_PLIST_PATH"
echo "Open http://127.0.0.1:15121/ on this Mac, or http://<mac-lan-ip>:15121/ from another device."
echo "Logs: $LOG_DIR/server.out.log and $LOG_DIR/server.err.log"
echo "Daily report logs: $LOG_DIR/daily-report.out.log and $LOG_DIR/daily-report.err.log"
