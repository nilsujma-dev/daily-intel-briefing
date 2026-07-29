#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.dailybriefing.agent"

# Remove every LaunchAgent that points at this install, whatever it is called.
# Matching on the install path rather than a fixed label means agents left behind
# by earlier versions are cleaned up too, instead of quietly firing forever.
remove_local_agents() {
  local app_dir="$1" removed=0 plist label
  for plist in "$HOME"/Library/LaunchAgents/*.plist; do
    [ -f "$plist" ] || continue
    if grep -q "$app_dir" "$plist" 2>/dev/null; then
      label="$(basename "$plist" .plist)"
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      rm -f "$plist"
      echo "$label"
      removed=$((removed + 1))
    fi
  done
  [ "$removed" -gt 0 ]
}

if REMOVED="$(remove_local_agents "$DIR")"; then
  echo "Removed: $(echo "$REMOVED" | tr '\n' ' ')"
else
  echo "No scheduled agent was installed for this directory."
fi
echo "Schedule removed. Your data in state/ and secrets/ is untouched."
