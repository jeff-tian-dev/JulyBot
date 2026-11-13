#!/usr/bin/env bash
# Install the netwatch launchd agent: every minute it records the Mac's TCP
# connection state to logs/netwatch/, so a network outage leaves evidence of
# what caused it. See deploy/netwatch.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.julybot.netwatch"
PLIST_SRC="$ROOT/deploy/com.julybot.netwatch.plist.template"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

mkdir -p "$ROOT/logs/netwatch" "$HOME/Library/LaunchAgents"
sed "s|__APP_DIR__|$ROOT|g" "$PLIST_SRC" > "$PLIST_DEST"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
  || launchctl unload "$PLIST_DEST" 2>/dev/null \
  || true

launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
launchctl enable "gui/$(id -u)/${LABEL}"

echo "Installed ${LABEL} (records connections every 60s)."
echo "  Logs:   $ROOT/logs/netwatch/"
echo "  Remove: ./deploy/uninstall-netwatch.sh"
