#!/usr/bin/env bash
# One-time Mac Studio setup: Python venv and .env.
# The database is hosted on Supabase — there is nothing to run locally.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Python venv + dependencies..."
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m ensurepip --upgrade 2>/dev/null || true
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt

mkdir -p data/bases logs

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env — fill in DISCORD_TOKEN, DISCORD_GUILD_ID, COC_API_TOKEN, and the Supabase DATABASE_URL."
else
  echo ".env already exists — leaving it unchanged."
fi

echo ""
echo "Setup complete on Mac Studio ($ROOT)."
echo "  1. Edit .env with your secrets"
echo "  2. .venv/bin/python scripts/init_db.py"
echo "  3. ./deploy/start.sh            # run in foreground"
echo "     ./deploy/install-service.sh  # auto-start on login via launchd"
