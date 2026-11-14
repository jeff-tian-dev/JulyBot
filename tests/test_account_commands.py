"""Unit tests for the account Cog layer: /accounts [user] and /adminlink."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from discord_bot.commands import account_commands

ADMIN = 555


def _interaction() -> MagicMock:
    inter = MagicMock()
    inter.author.id = ADMIN
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    return inter


def _user(user_id: int, *, bot: bool = False) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.bot = bot
    user.mention = f"<@{user_id}>"
    user.display_name = f"user{user_id}"
    return user


def _cog() -> account_commands.AccountCommands:
    cog = account_commands.AccountCommands(MagicMock())
    cog.bot.pool = MagicMock()
    return cog


def _reply(inter) -> str:
    return inter.edit_original_response.await_args.kwargs.get("content", "")


@pytest.mark.asyncio
async def test_accounts_without_a_user_shows_your_own() -> None:
    inter = _interaction()
    cog = _cog()
    lookup = AsyncMock(return_value=[{"coc_tag": "#A", "coc_name": "Me", "verified": True}])

    with patch.object(account_commands, "get_linked_accounts", lookup):
        await cog.accounts.callback(cog, inter, user=None)

    assert lookup.await_args.args[1] == ADMIN
    embed = inter.edit_original_response.await_args.kwargs["embed"]
    assert embed.title == "Your linked CoC accounts"


@pytest.mark.asyncio
async def test_accounts_with_a_user_shows_theirs() -> None:
    """Replaces the old /whois."""
    inter = _interaction()
    cog = _cog()
    other = _user(77)
    lookup = AsyncMock(return_value=[{"coc_tag": "#B", "coc_name": "Them", "verified": False}])

    with patch.object(account_commands, "get_linked_accounts", lookup):
        await cog.accounts.callback(cog, inter, user=other)

    assert lookup.await_args.args[1] == 77
    embed = inter.edit_original_response.await_args.kwargs["embed"]
    assert embed.title == "user77's linked CoC accounts"


@pytest.mark.asyncio
async def test_accounts_for_a_user_with_no_links_names_them() -> None:
    inter = _interaction()
    cog = _cog()

    with patch.object(account_commands, "get_linked_accounts", AsyncMock(return_value=[])):
        await cog.accounts.callback(cog, inter, user=_user(77))

    assert "<@77> has no linked CoC accounts" in _reply(inter)


def test_whois_is_gone() -> None:
    cog = _cog()
    assert not hasattr(cog, "whois")


@pytest.mark.asyncio
async def test_adminlink_refuses_a_bot() -> None:
    inter = _interaction()
    cog = _cog()
    link = AsyncMock()

    with patch.object(account_commands, "admin_link_account", link):
        await cog.adminlink.callback(cog, inter, user=_user(77, bot=True), coc_tag="#A")

    link.assert_not_awaited()
    assert "Bots" in _reply(inter)


@pytest.mark.asyncio
async def test_adminlink_refuses_someone_outside_the_server() -> None:
    inter = _interaction()
    inter.guild.get_member = MagicMock(return_value=None)
    cog = _cog()
    link = AsyncMock()

    with patch.object(account_commands, "admin_link_account", link):
        await cog.adminlink.callback(cog, inter, user=_user(77), coc_tag="#A")

    link.assert_not_awaited()
    assert "isn't a member" in _reply(inter)


@pytest.mark.asyncio
async def test_adminlink_links_and_says_it_is_unverified() -> None:
    inter = _interaction()
    cog = _cog()
    link = AsyncMock(return_value={
        "success": True, "coc_tag": "#2PP0JCCL", "coc_name": "Chief",
        "already": False, "verified": False,
    })

    with patch.object(account_commands, "admin_link_account", link):
        await cog.adminlink.callback(cog, inter, user=_user(77), coc_tag="2pp0jccl")

    assert link.await_args.args[1:] == (77, "2pp0jccl", ADMIN)
    assert "unverified" in _reply(inter) and "Chief" in _reply(inter)
    allowed = inter.edit_original_response.await_args.kwargs["allowed_mentions"]
    assert allowed.users is False


@pytest.mark.asyncio
async def test_adminlink_names_who_already_holds_the_tag() -> None:
    inter = _interaction()
    cog = _cog()
    link = AsyncMock(return_value={
        "success": False, "error": "#2PP0JCCL is already linked to another Discord user.",
        "holder_id": 88,
    })

    with patch.object(account_commands, "admin_link_account", link):
        await cog.adminlink.callback(cog, inter, user=_user(77), coc_tag="#2PP0JCCL")

    assert "<@88>" in _reply(inter) and "/unlink" in _reply(inter)


@pytest.mark.asyncio
async def test_adminlink_reports_an_existing_link_without_changing_it() -> None:
    inter = _interaction()
    cog = _cog()
    link = AsyncMock(return_value={
        "success": True, "coc_tag": "#2PP0JCCL", "coc_name": "Chief",
        "already": True, "verified": True,
    })

    with patch.object(account_commands, "admin_link_account", link):
        await cog.adminlink.callback(cog, inter, user=_user(77), coc_tag="#2PP0JCCL")

    assert "already linked" in _reply(inter) and "(verified)" in _reply(inter)
