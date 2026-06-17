"""One-off: check alert channel config and dump latest webhook payload for debugging."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from database.connection import get_pool
from modules.twitter_stalker.accounts import get_config, list_accounts


async def main() -> None:
    pool = await get_pool()
    cfg = await get_config(pool)
    print("Config:", dict(cfg) if cfg else "No config row")
    accounts = await list_accounts(pool)
    print("Stalked accounts:", [dict(a) for a in accounts])
    await pool.close()


asyncio.run(main())
