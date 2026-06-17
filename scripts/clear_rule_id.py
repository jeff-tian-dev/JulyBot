"""One-off: clear the stored filter rule_id so the bot recreates it fresh."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from database.connection import get_pool
from modules.twitter_stalker.accounts import save_filter_rule_id


async def main() -> None:
    pool = await get_pool()
    await save_filter_rule_id(pool, None)
    print("Cleared rule_id from DB — bot will recreate on next restart.")
    await pool.close()


asyncio.run(main())
