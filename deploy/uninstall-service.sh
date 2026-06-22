#!/usr/bin/env bash
# Remove the launchd user agent for JulyBot.
set -euo pipefail

LABEL="com.julybot"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ -f "$PLIST" ]]; then
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
    || launchctl unload "$PLIST" 2>/dev/null \
    || true
  rm -f "$PLIST"
  echo "Removed ${LABEL}."
else
  echo "No launchd service found at $PLIST."
fi
