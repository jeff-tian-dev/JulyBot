#!/usr/bin/env bash
# Remove BOTH launchd agents in one command: the bot and the subscription
# website. Convenience wrapper — see install-service-all.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

"$ROOT/deploy/uninstall-service.sh"
"$ROOT/deploy/uninstall-service-web.sh"
