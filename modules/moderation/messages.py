"""Public taunt messages for kick/ban commands."""
from __future__ import annotations

import random

import disnake

BAN_QUIPS: tuple[str, ...] = (
    "we had a vote. it wasn't close.",
    "you speedran the rules.",
    "the council has spoken.",
    "this isn't the server for you.",
    "complete imbecile",
    "fly, be free. But not here.",
)

KICK_QUIPS: tuple[str, ...] = (
    "the next one is permanent.",
    "go to your room.",
    "you know what you did.",
    "this isn't the you we know.",
    "kicked with love. mostly.",
)


def pick_ban_quip() -> str:
    return random.choice(BAN_QUIPS)


def pick_kick_quip() -> str:
    return random.choice(KICK_QUIPS)


def format_public_message(user: disnake.User | disnake.Member, quip: str) -> str:
    return f"{user} {quip}"
