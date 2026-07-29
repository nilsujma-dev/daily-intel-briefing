#!/bin/bash
# Installs (or reinstalls) the 08:00 daily LaunchAgent.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.dailybriefing.agent"
AGENT_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENT_DIR/$LABEL.plist"

mkdir -p "$AGENT_DIR" "$DIR/state/logs"

sed "s|__APP_DIR__|$DIR|g" "$DIR/com.dailybriefing.agent.plist.template" > "$PLIST"
chmod 644 "$PLIST"

UID_NUM="$(id -u)"
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl enable "gui/$UID_NUM/$LABEL"

echo "Installed: $PLIST"
echo "Schedule : daily at 08:00 local time"
echo
echo "Verify with :  launchctl print gui/$UID_NUM/$LABEL | head -20"
echo "Run now with:  launchctl kickstart -k gui/$UID_NUM/$LABEL"
echo "Remove with :  ./uninstall-schedule.sh"
