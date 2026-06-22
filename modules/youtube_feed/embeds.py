"""Discord embed formatting for YouTube videos."""
from __future__ import annotations

import disnake

from modules.youtube_feed.fetcher import VideoEntry

THUMBNAIL_URL = "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def build_video_embed(entry: VideoEntry) -> disnake.Embed:
    """Build a Discord embed for a new YouTube video."""
    embed = disnake.Embed(
        title=entry.title,
        url=entry.url,
        color=disnake.Color.red(),
    )
    embed.set_author(name=entry.channel_title)
    embed.set_image(url=THUMBNAIL_URL.format(video_id=entry.video_id))
    if entry.published is not None:
        embed.timestamp = entry.published
    embed.set_footer(text="YouTube")
    return embed
