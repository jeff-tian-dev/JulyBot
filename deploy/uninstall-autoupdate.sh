#!/usr/bin/env bash
# Remove the auto-update launchd agent. The bot itself keeps running.
set -euo pipefail

LABEL="com.julybot.autoupdate"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ -f "$PLIST" ]]; then
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
    || launchctl unload "$PLIST" 2>/dev/null \
    || true
  rm -f "$PLIST"
  echo "Removed ${LABEL}. Pushes will no longer deploy automatically."
else
  echo "No launchd agent found at $PLIST."
fi
