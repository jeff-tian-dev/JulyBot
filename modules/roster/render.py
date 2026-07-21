"""Shared rendering for the roster list.

Both the ephemeral `/roster view` command and the auto-posted clan-watch board
render the same numbered inline-code bubbles, so the formatting lives here (a
module) rather than in the Discord cog — the cog and the watcher both import it.
"""
from __future__ import annotations

import disnake

from modules.roster import storage

ROSTER_COLOR = disnake.Color.from_rgb(240, 178, 50)  # Clash-y gold
DESC_LIMIT = 3800  # keep each embed description under the 4096 cap

# Column key -> (button label, max width). status/total only apply to watched
# rosters (they carry the clan-absence data the watcher maintains).
COLUMN_SPECS = {
    "user": ("User", 14),
    "ign": ("IGN", 14),
    "clan": ("Clan", 14),
    "status": ("Status", 12),
    "total": ("Total Out", 10),
    "trophies": ("Trophies", 5),
    "check": ("In clan", 2),
    "out": ("Time Out", 3),
    "coctag": ("CoC Tag", 10),
    "discord": ("Discord", 14),
}
BASE_COLUMNS = ("user", "ign", "clan")
WATCHED_COLUMNS = ("user", "ign", "clan", "status", "total")
# Watched-roster leaderboard columns (used by the daily board), in order.
LEADERBOARD_COLUMNS = ("trophies", "check", "out", "ign")
# /roster view columns (every roster), in order. CoC tag + Discord start hidden.
VIEW_COLUMNS = ("trophies", "coctag", "ign", "discord")
VIEW_HIDDEN = ("coctag", "discord")
# Columns that start hidden; "total" only exists on watched rosters but listing
# it here is harmless when it's not a column.
DEFAULT_HIDDEN = ("user", "total")

# CoC clan/player names are often Arabic (RTL). Rendered on one line, the Unicode
# bidi algorithm reorders the neutral chars (spaces, digits, the status emoji)
# around an RTL run, so a clan name visually bleeds into the neighbouring column.
# Wrapping every cell in a LEFT-TO-RIGHT ISOLATE / POP DIRECTIONAL ISOLATE pair
# fences each column off so nothing reorders across cell boundaries.
_LRI = "⁦"
_PDI = "⁩"


def _isolate(text: str) -> str:
    return f"{_LRI}{text}{_PDI}"


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def coc_cells(m: dict) -> tuple[str, str, str]:
    """The (tag, ign, clan) columns for a member, with the Unlinked/Unknown rules."""
    tag = m["coc_tag"] or "Unlinked"
    if not m["coc_tag"]:
        ign = clan = "Unlinked"
    else:
        ign = m["coc_name"] if m["coc_api_ok"] else "Unknown"
        clan = (m["clan_name"] or "No clan") if m["coc_api_ok"] else "Unknown"
    return tag, ign, clan


def status_cell(m: dict) -> str:
    """In-clan status + current absence for the Status column (watched rosters)."""
    if m["in_main_clan"] is None:
        return "❔"
    if m["in_main_clan"]:
        return "✅ In"
    dur = storage.format_duration(m["out_seconds"])
    return f"❌ {dur}" if dur else "❌ Out"


def total_cell(m: dict) -> str:
    """Lifetime absence for the Total Out column."""
    return storage.format_duration(m["total_seconds"]) or "—"


def member_row(m: dict, watched: bool, user_label: str) -> dict:
    """Build the {user, ign, clan, [status, total]} cell dict for one member."""
    _tag, ign, clan = coc_cells(m)
    row = {"user": user_label, "ign": ign, "clan": clan}
    if watched:
        row["status"] = status_cell(m)
        row["total"] = total_cell(m)
    return row


def default_show(columns) -> dict:
    """The initial show/hide map for a set of columns (DEFAULT_HIDDEN start off)."""
    return {key: key not in DEFAULT_HIDDEN for key in columns}


def _bubble(index: int, row: dict, columns, show: dict) -> str:
    """One numbered account bubble showing only the currently-enabled columns."""
    parts = [_isolate(_truncate(row[key], COLUMN_SPECS[key][1])) for key in columns if show[key]]
    # Single-digit ranks get an extra space so their columns line up with 10+.
    gap = "  " if index < 10 else " "
    return "`" + f"{index}." + (gap + "  ".join(parts) if parts else "") + "`"


def render_embed(name: str, rows: list[dict], columns, show: dict) -> disnake.Embed:
    """Single embed listing every account as a numbered bubble (enabled columns only)."""
    lines = [_bubble(i, row, columns, show) for i, row in enumerate(rows, start=1)]
    desc = "\n".join(lines)
    if len(desc) > DESC_LIMIT:
        desc = desc[:DESC_LIMIT].rsplit("\n", 1)[0] + "\n… (list truncated)"

    embed = disnake.Embed(title=f"📋 {name}", description=desc or "_empty_", color=ROSTER_COLOR)
    legend = " · ".join(COLUMN_SPECS[key][0] for key in columns if show[key])
    total = len(rows)
    count = f"{total} account{'s' if total != 1 else ''}"
    embed.set_footer(text=f"{legend}  •  {count}" if legend else count)
    return embed


def _out_compact(seconds: int) -> str:
    """A ≤3-char daily out-time, right-padded (e.g. ' 2h', '45m', '12h', ' 0m')."""
    seconds = int(seconds or 0)
    if seconds >= 3600:
        text = f"{seconds // 3600}h"
    else:
        text = f"{seconds // 60}m"
    return text.rjust(3)


def leaderboard_cells(raw: dict) -> dict:
    """Convert a build_daily_rows entry into rendered cell strings for LEADERBOARD_COLUMNS."""
    return {
        "trophies": str(raw["trophies"]),
        "check": "✅" if raw["in_family"] else "❌",
        "out": _out_compact(raw["daily_seconds"]),
        "ign": raw["ign"],
    }


def leaderboard_embed(name: str, raw_rows: list[dict]) -> disnake.Embed:
    """Static (no-buttons) leaderboard embed for the daily board."""
    rows = [leaderboard_cells(r) for r in raw_rows]
    show = {key: True for key in LEADERBOARD_COLUMNS}
    return render_embed(name, rows, LEADERBOARD_COLUMNS, show)


def view_cells(m: dict, discord_label: str) -> dict:
    """Cells for /roster view (VIEW_COLUMNS) from a build_roster_view member."""
    tag = m["coc_tag"] or "Unlinked"
    if not m["coc_tag"]:
        ign = "Unlinked"
    else:
        ign = m["coc_name"] if m["coc_api_ok"] else "Unknown"
    trophies = str(m["trophies"]) if m.get("trophies") is not None else "—"
    return {"trophies": trophies, "coctag": tag, "ign": ign, "discord": discord_label}
