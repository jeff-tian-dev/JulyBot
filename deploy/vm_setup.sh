#!/usr/bin/env bash
# Lightweight VM setup: Discord bot + webhook only. Database is external (Supabase).
set -euo pipefail

APP_DIR=/opt/julybot

if [[ ! -f "$APP_DIR/main.py" ]]; then
  echo "ERROR: sync repo to $APP_DIR first."
  exit 1
fi

cd "$APP_DIR"
sed -i 's/\r$//' deploy/*.sh 2>/dev/null || true

echo "==> Ensuring swap (helps pip on 512MB VMs)..."
if ! swapon --show | grep -q '/swapfile'; then
  if [[ ! -f /swapfile ]]; then
    sudo fallocate -l 1G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
  fi
  sudo swapon /swapfile 2>/dev/null || true
  grep -q '^/swapfile ' /etc/fstab 2>/dev/null || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

echo "==> Python venv + dependencies..."
rm -rf .venv
python3 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements-twitter.txt

if [[ ! -f .env ]]; then
  cp deploy/env.twitter.example .env
  echo "Created .env — edit DATABASE_URL (Supabase) and DISCORD_* before starting."
fi

echo "==> systemd service..."
sudo cp deploy/julybot-twitter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable julybot-twitter

echo ""
echo "Setup complete."
echo "  1. Edit $APP_DIR/.env (Supabase DATABASE_URL, DISCORD_TOKEN, DISCORD_GUILD_ID)"
echo "  2. Run init_db once: cd $APP_DIR && .venv/bin/python scripts/init_db.py"
echo "  3. sudo systemctl start julybot-twitter"
