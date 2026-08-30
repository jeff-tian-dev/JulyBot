#!/usr/bin/env bash
# Stop the launchd-managed web service (if installed). Does not affect the bot.
set -euo pipefail

LABEL="com.julybot.web"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ -f "$PLIST" ]]; then
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
    || launchctl unload "$PLIST" 2>/dev/null \
    || true
  echo "Stopped launchd service ${LABEL}."
else
  echo "No launchd service installed at $PLIST."
  echo "If running in a terminal, stop with Ctrl-C."
fi
