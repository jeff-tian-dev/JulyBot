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
os.environ.setdefault("X_COOKIES", "")
os.environ.setdefault("X_SESSION_NAME", "julybot_x")
os.environ.setdefault("X_POLL_INTERVAL_MINUTES", "10")
os.environ.setdefault("X_PING_ROLE_ID", "0")
os.environ.setdefault("YOUTUBE_FEED_POLL_INTERVAL_MINUTES", "10")
os.environ.setdefault("YOUTUBE_PING_ROLE_ID", "0")
os.environ.setdefault("MOD_LOG_CHANNEL_ID", "1514111681222148219")
