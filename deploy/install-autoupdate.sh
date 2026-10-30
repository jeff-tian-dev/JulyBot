#!/usr/bin/env bash
# Install the auto-update launchd agent: every minute it pulls origin/main and,
# if it moved and the tests pass, restarts the bot. See deploy/auto-update.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.julybot.autoupdate"
PLIST_SRC="$ROOT/deploy/com.julybot.autoupdate.plist.template"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "ERROR: .venv not found. Run ./deploy/setup.sh first."
  exit 1
fi

mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"
sed "s|__APP_DIR__|$ROOT|g" "$PLIST_SRC" > "$PLIST_DEST"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
  || launchctl unload "$PLIST_DEST" 2>/dev/null \
  || true

launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
launchctl enable "gui/$(id -u)/${LABEL}"

echo "Installed ${LABEL} (checks GitHub every 60s)."
echo "  Log:    $ROOT/logs/auto-update.log"
echo "  Remove: ./deploy/uninstall-autoupdate.sh"
