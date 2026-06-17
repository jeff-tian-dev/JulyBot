"""Build and send Discord embeds for new tweets."""
from __future__ import annotations

import logging
from typing import Any

import disnake

logger = logging.getLogger(__name__)

MAX_EMBED_DESCRIPTION = 4000


def build_tweet_embed(tweet: dict[str, Any]) -> disnake.Embed:
    """Format a parsed tweet dict as a Discord embed."""
    username = tweet.get("username", "unknown")
    display_name = tweet.get("display_name") or username
    text = tweet.get("text") or ""
    tweet_id = tweet.get("tweet_id", "")
    url = tweet.get("url") or f"https://x.com/{username}/status/{tweet_id}"

    if len(text) > MAX_EMBED_DESCRIPTION:
        text = text[: MAX_EMBED_DESCRIPTION - 3] + "..."

    embed = disnake.Embed(
        description=text or "(no text)",
        url=url,
        color=disnake.Color.from_rgb(29, 161, 242),
    )
    embed.set_author(name=f"{display_name} (@{username})", url=f"https://x.com/{username}")
    embed.set_footer(text="New post on X")

    media_url = tweet.get("media_url")
    if media_url:
        embed.set_image(url=media_url)

    return embed


async def send_tweet_alert(
    bot: disnake.Client,
    channel_id: int,
    tweet: dict[str, Any],
) -> None:
    await bot.wait_until_ready()
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except disnake.NotFound:
            logger.error("Alert channel %s not found", channel_id)
            return
        except disnake.Forbidden:
            logger.error("No access to alert channel %s", channel_id)
            return
    if not isinstance(channel, disnake.TextChannel):
        logger.error("Channel %s is not a text channel (type=%s)", channel_id, type(channel).__name__)
        return

    embed = build_tweet_embed(tweet)
    await channel.send(embed=embed)
    logger.info(
        "Posted tweet alert for @%s (id=%s) to channel %s",
        tweet.get("username"),
        tweet.get("tweet_id"),
        channel_id,
    )
