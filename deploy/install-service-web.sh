#!/usr/bin/env bash
# Install the subscription website as a launchd user agent (auto-start on login).
# Independent of the bot's own service (com.julybot) — installing/restarting one
# does not affect the other.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.julybot.web"
PLIST_SRC="$ROOT/deploy/com.julybot.web.plist.template"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ ! -f "$ROOT/.env" ]]; then
  echo "ERROR: .env not found. Run ./deploy/setup.sh first."
  exit 1
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "ERROR: .venv not found. Run ./deploy/setup.sh first."
  exit 1
fi

mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"
sed "s|__APP_DIR__|$ROOT|g" "$PLIST_SRC" > "$PLIST_DEST"

# Stop an existing instance before reloading.
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
  || launchctl unload "$PLIST_DEST" 2>/dev/null \
  || true

launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
launchctl enable "gui/$(id -u)/${LABEL}"
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

echo "Installed and started ${LABEL}."
echo "  Logs: $ROOT/logs/julybot-web.{stdout,stderr}.log"
echo "  Stop:  ./deploy/stop-web.sh"
echo "  Remove: ./deploy/uninstall-service-web.sh"
