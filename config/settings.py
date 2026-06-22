"""Centralized configuration loaded from environment variables.

Every other module imports the `settings` singleton from this file.
Nothing else in the codebase should call os.getenv or load_dotenv directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


REQUIRED_VARS = ("DISCORD_TOKEN", "COC_API_TOKEN", "DATABASE_URL")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(
            f"Required environment variable {name!r} is missing. "
            f"Copy .env.example to .env and fill in values."
        )
    return value


def _optional(name: str, default: str) -> str:
    return os.getenv(name) or default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"Environment variable {name!r} must be an int, got {raw!r}") from e


def _csv(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    DISCORD_TOKEN: str
    DISCORD_GUILD_ID: int
    COC_API_TOKEN: str
    COC_API_BASE_URL: str
    DATABASE_URL: str
    BASE_IMAGE_DIR: str
    BASE_CACHE_SIZE: int
    YOUTUBE_CHANNEL_IDS: list[str] = field(default_factory=list)
    LEGEND_POLL_INTERVAL_MINUTES: int = 60
    CACHE_REFRESH_INTERVAL_HOURS: int = 24
    TWITTER_COOKIES: str = ""
    TWITTER_SESSION_NAME: str = "julybot_twitter"
    TWITTER_POLL_INTERVAL_MINUTES: int = 10
    TWITTER_PING_ROLE_ID: int = 0
    YOUTUBE_FEED_POLL_INTERVAL_MINUTES: int = 10
    MOD_LOG_CHANNEL_ID: int = 0


def _load() -> Settings:
    for var in REQUIRED_VARS:
        _require(var)

    guild_raw = os.getenv("DISCORD_GUILD_ID", "0")
    try:
        guild_id = int(guild_raw)
    except ValueError as e:
        raise ValueError(f"DISCORD_GUILD_ID must be an integer, got {guild_raw!r}") from e

    return Settings(
        DISCORD_TOKEN=_require("DISCORD_TOKEN"),
        DISCORD_GUILD_ID=guild_id,
        COC_API_TOKEN=_require("COC_API_TOKEN"),
        COC_API_BASE_URL=_optional("COC_API_BASE_URL", "https://api.clashofclans.com/v1"),
        DATABASE_URL=_require("DATABASE_URL"),
        BASE_IMAGE_DIR=_optional("BASE_IMAGE_DIR", "./data/bases"),
        BASE_CACHE_SIZE=_int("BASE_CACHE_SIZE", 750),
        YOUTUBE_CHANNEL_IDS=_csv("YOUTUBE_CHANNEL_IDS"),
        LEGEND_POLL_INTERVAL_MINUTES=_int("LEGEND_POLL_INTERVAL_MINUTES", 60),
        CACHE_REFRESH_INTERVAL_HOURS=_int("CACHE_REFRESH_INTERVAL_HOURS", 24),
        TWITTER_COOKIES=_optional("TWITTER_COOKIES", ""),
        TWITTER_SESSION_NAME=_optional("TWITTER_SESSION_NAME", "julybot_twitter"),
        TWITTER_POLL_INTERVAL_MINUTES=_int("TWITTER_POLL_INTERVAL_MINUTES", 10),
        TWITTER_PING_ROLE_ID=_int("TWITTER_PING_ROLE_ID", 0),
        YOUTUBE_FEED_POLL_INTERVAL_MINUTES=_int("YOUTUBE_FEED_POLL_INTERVAL_MINUTES", 10),
        MOD_LOG_CHANNEL_ID=_int("MOD_LOG_CHANNEL_ID", 1514111681222148219),
    )


settings: Settings = _load()
