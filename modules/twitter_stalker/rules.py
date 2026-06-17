"""Sync stalked accounts to a twitterapi.io webhook filter rule."""
from __future__ import annotations

import logging

import asyncpg

from config.settings import settings
from modules.twitter_stalker import api
from modules.twitter_stalker.accounts import (
    get_filter_rule_id,
    list_usernames,
    save_filter_rule_id,
)
from modules.twitter_stalker.filter_query import build_filter_value, validate_filter_capacity

logger = logging.getLogger(__name__)


async def sync_filter_rule(pool: asyncpg.Pool) -> None:
    """Reconcile DB stalk list with twitterapi.io filter rule."""
    if not api.api_key_configured():
        logger.warning("TWITTERAPI_IO_KEY not set; skipping filter rule sync")
        return

    usernames = await list_usernames(pool)
    validate_filter_capacity(usernames)

    tag = settings.TWITTER_FILTER_TAG
    interval = settings.TWITTER_FILTER_INTERVAL_SECONDS
    rule_id = await get_filter_rule_id(pool)

    if not usernames:
        if rule_id:
            try:
                await api.delete_filter_rule(rule_id)
            except api.TwitterApiError:
                logger.exception("Failed to delete filter rule %s", rule_id)
            await save_filter_rule_id(pool, None)
            logger.info("Deleted empty filter rule")
        return

    value = build_filter_value(usernames)

    if rule_id:
        try:
            await api.update_filter_rule(rule_id, tag, value, interval)
            logger.info("Updated filter rule %s for %d account(s)", rule_id, len(usernames))
            return
        except api.TwitterApiError:
            logger.warning("Failed to update rule %s (stale?); deleting and recreating", rule_id)
            try:
                await api.delete_filter_rule(rule_id)
            except api.TwitterApiError:
                logger.warning("Could not delete stale rule %s (already gone?)", rule_id)
            await save_filter_rule_id(pool, None)

    new_rule_id = await api.add_filter_rule(tag, value, interval)
    await save_filter_rule_id(pool, new_rule_id)
    logger.info("Created filter rule %s for %d account(s)", new_rule_id, len(usernames))
    # Activate the new rule ("on air")
    try:
        await api.update_filter_rule(new_rule_id, tag, value, interval, activate=True)
        logger.info("Activated filter rule %s", new_rule_id)
    except api.TwitterApiError:
        logger.warning("Could not activate rule %s; it may stay on standby", new_rule_id)
