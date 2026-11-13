#!/usr/bin/env bash
# Remove the netwatch launchd agent. Its logs in logs/netwatch/ are kept.
set -euo pipefail

LABEL="com.julybot.netwatch"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ -f "$PLIST" ]]; then
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
    || launchctl unload "$PLIST" 2>/dev/null \
    || true
  rm -f "$PLIST"
  echo "Removed ${LABEL}. Connection state is no longer recorded."
else
  echo "No launchd agent found at $PLIST."
fi
