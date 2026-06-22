#!/usr/bin/env bash
# Run the bot in the foreground. The database is hosted on Supabase.
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

exec .venv/bin/python main.py
