"""Discord embed formatting for tweets."""
from __future__ import annotations

import re

import disnake

EMBED_DESCRIPTION_LIMIT = 4096
MAX_MEDIA_EMBEDS = 4

_TCO_URL_RE = re.compile(r"https?://t\.co/\S+", re.IGNORECASE)
_PIC_X_URL_RE = re.compile(r"https?://pic\.x\.com/\S+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _normalize_x_url(url: str) -> str:
    return url.replace("https://twitter.com/", "https://x.com/").replace(
        "http://twitter.com/", "https://x.com/"
    )


def _tweet_url(tweet) -> str:
    if getattr(tweet, "is_retweet", False):
        retweeted = getattr(tweet, "retweeted_tweet", None)
        if retweeted is not None:
            url = getattr(retweeted, "url", None)
            if url:
                return _normalize_x_url(str(url))

    url = getattr(tweet, "url", None)
    if url:
        return _normalize_x_url(str(url))
    author = getattr(tweet, "author", None)
    username = getattr(author, "username", "i") if author else "i"
    tweet_id = getattr(tweet, "id", "")
    return f"https://x.com/{username}/status/{tweet_id}"


def _profile_image_url(author) -> str | None:
    """Best-effort profile picture URL for the embed author icon."""
    if author is None:
        return None

    for attr in ("profile_image_url_https", "profile_image_url"):
        url = getattr(author, attr, None)
        if url:
            return str(url).replace("_normal.", "_400x400.")

    for container_attr in ("_original_user", "_user", "_raw"):
        container = getattr(author, container_attr, None)
        if isinstance(container, dict):
            url = container.get("profile_image_url_https") or container.get("profile_image_url")
            if url:
                return str(url).replace("_normal.", "_400x400.")

    username = getattr(author, "username", None)
    if username:
        return f"https://unavatar.io/x/{username}"

    return None


def _media_image_urls(tweet) -> list[str]:
    """Return displayable image/thumbnail URLs from tweet media attachments."""
    urls: list[str] = []
    for item in getattr(tweet, "media", None) or []:
        media_type = getattr(item, "type", None)
        url = getattr(item, "media_url_https", None) or getattr(item, "direct_url", None)
        if not url:
            continue
        url = str(url)
        if url in urls:
            continue
        if media_type in (None, "photo", "video", "animated_gif"):
            urls.append(url)
    return urls


def _clean_tweet_text(tweet) -> str:
    """Remove media/t.co links from tweet text; those are shown separately in the embed."""
    raw = (getattr(tweet, "text", None) or "").strip()
    if not raw:
        return ""

    cleaned = raw
    for item in getattr(tweet, "media", None) or []:
        for attr in ("url", "display_url", "expanded_url"):
            val = getattr(item, attr, None)
            if not val:
                continue
            val = str(val)
            cleaned = cleaned.replace(val, " ")
            if not val.startswith("http"):
                cleaned = cleaned.replace(f"https://{val}", " ")

    cleaned = _TCO_URL_RE.sub(" ", cleaned)
    cleaned = _PIC_X_URL_RE.sub(" ", cleaned)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def _author_labels(author) -> tuple[str, str]:
    display_name = getattr(author, "name", None) or getattr(author, "username", "Unknown")
    username = getattr(author, "username", "unknown")
    return display_name, username


def _embed_author_line(tweet) -> tuple[str, str | None, str | None]:
    """Return author display line, profile URL, and icon URL for the main embed."""
    author = getattr(tweet, "author", None)
    display_name, username = _author_labels(author)
    icon_url = _profile_image_url(author)
    profile_url = f"https://x.com/{username}"

    if getattr(tweet, "is_retweet", False):
        retweeted = getattr(tweet, "retweeted_tweet", None)
        if retweeted is not None:
            rt_author = getattr(retweeted, "author", None)
            rt_name, rt_user = _author_labels(rt_author)
            icon_url = _profile_image_url(rt_author) or icon_url
            author_line = f"{display_name} (@{username}) reposted {rt_name} (@{rt_user})"
            return author_line, profile_url, icon_url

    return f"{display_name} (@{username})", profile_url, icon_url


def _quoted_embed(quoted_tweet) -> disnake.Embed | None:
    author = getattr(quoted_tweet, "author", None)
    if author is None:
        return None

    display_name, username = _author_labels(author)
    text = _clean_tweet_text(quoted_tweet)
    media_urls = _media_image_urls(quoted_tweet)
    description = _truncate(text, EMBED_DESCRIPTION_LIMIT) if text else None

    embed = disnake.Embed(description=description, colour=0x15202B)
    embed.set_author(
        name=f"Quoted: {display_name} (@{username})",
        url=f"https://x.com/{username}",
        icon_url=_profile_image_url(author),
    )
    if media_urls:
        embed.set_image(url=media_urls[0])
    return embed


def build_tweet_embeds(tweet) -> list[disnake.Embed]:
    """Build one or more Discord embeds for a tweety Tweet object."""
    author = getattr(tweet, "author", None)
    _, username = _author_labels(author)

    text = _clean_tweet_text(tweet)
    media_urls = _media_image_urls(tweet)

    description = _truncate(text, EMBED_DESCRIPTION_LIMIT) if text else None

    author_line, profile_url, icon_url = _embed_author_line(tweet)
    embed = disnake.Embed(description=description, colour=0x1D9BF0)
    embed.set_author(name=author_line, url=profile_url, icon_url=icon_url)

    if media_urls:
        embed.set_image(url=media_urls[0])

    embed.set_footer(text=f"@{username}")

    created_on = getattr(tweet, "created_on", None)
    if created_on is not None:
        embed.timestamp = created_on

    embeds = [embed]
    for image_url in media_urls[1:MAX_MEDIA_EMBEDS]:
        extra = disnake.Embed()
        extra.set_image(url=image_url)
        embeds.append(extra)

    if getattr(tweet, "is_quoted", False) and not getattr(tweet, "is_retweet", False):
        quoted = getattr(tweet, "quoted_tweet", None)
        if quoted is not None:
            quoted_embed = _quoted_embed(quoted)
            if quoted_embed is not None:
                embeds.append(quoted_embed)

    return embeds


def build_tweet_components(tweet) -> list[disnake.ui.ActionRow]:
    """Link button below the embed (Discord footers cannot contain clickable links)."""
    return [
        disnake.ui.ActionRow(
            disnake.ui.Button(
                style=disnake.ButtonStyle.link,
                label="View on X",
                url=_tweet_url(tweet),
            )
        )
    ]


def build_tweet_message(tweet) -> tuple[list[disnake.Embed], list[disnake.ui.ActionRow]]:
    """Build embeds and the View-on-X button row for a Discord message."""
    return build_tweet_embeds(tweet), build_tweet_components(tweet)


def build_tweet_embed(tweet) -> disnake.Embed:
    """Build the primary Discord embed for a tweet."""
    return build_tweet_embeds(tweet)[0]
