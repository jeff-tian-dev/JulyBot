#!/usr/bin/env bash
# Run the subscription website in the foreground. Separate process from the bot.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Run ./deploy/setup.sh first."
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "ERROR: .venv not found. Run ./deploy/setup.sh first."
  exit 1
fi

exec .venv/bin/python web/main.py
