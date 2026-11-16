#!/usr/bin/env bash
# Install Blossom as a launchd user agent (auto-start on login).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.blossom"
PLIST_SRC="$ROOT/deploy/com.blossom.plist.template"
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
echo "  Logs: $ROOT/logs/blossom.{stdout,stderr}.log"
echo "  Stop:  ./deploy/stop.sh"
echo "  Remove: ./deploy/uninstall-service.sh"
