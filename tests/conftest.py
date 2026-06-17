"""Pytest setup: stub required env vars before any project import."""
from __future__ import annotations

import os

os.environ.setdefault("DISCORD_TOKEN", "test-discord-token")
os.environ.setdefault("DISCORD_GUILD_ID", "0")
os.environ.setdefault("COC_API_TOKEN", "test-coc-token")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("BASE_IMAGE_DIR", "./data/bases")
os.environ.setdefault("BASE_CACHE_SIZE", "750")
os.environ.setdefault("YOUTUBE_CHANNEL_IDS", "")
os.environ.setdefault("TWITTERAPI_IO_KEY", "test-twitterapi-key")
os.environ.setdefault("TWITTER_WEBHOOK_HOST", "0.0.0.0")
os.environ.setdefault("TWITTER_WEBHOOK_PORT", "8080")
os.environ.setdefault("TWITTER_WEBHOOK_PATH", "/webhooks/twitter")
os.environ.setdefault("TWITTER_FILTER_INTERVAL_SECONDS", "60")
os.environ.setdefault("TWITTER_FILTER_TAG", "julybot-stalk")
os.environ.setdefault("TWITTER_ALLOWED_ROLE_IDS", "")
