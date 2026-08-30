#!/usr/bin/env bash
# Install (or restart) BOTH launchd services in one command: the bot
# (com.julybot) and the subscription website (com.julybot.web). A convenience
# wrapper only — the two remain independent launchd services under the hood,
# so a crash or manual stop of one still doesn't affect the other; this script
# just saves typing both commands separately after a code deploy.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

"$ROOT/deploy/install-service.sh"
echo ""
"$ROOT/deploy/install-service-web.sh"
echo ""
echo "Both services installed and started."
echo "  Stop both:  ./deploy/stop-all.sh"
echo "  Remove both: ./deploy/uninstall-service-all.sh"
