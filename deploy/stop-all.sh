#!/usr/bin/env bash
# Stop BOTH launchd services in one command: the bot and the subscription
# website. Convenience wrapper — see install-service-all.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

"$ROOT/deploy/stop.sh"
"$ROOT/deploy/stop-web.sh"
