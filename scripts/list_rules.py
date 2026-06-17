"""One-off: list and clean up duplicate twitterapi.io filter rules."""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import aiohttp

API_KEY = os.environ.get("TWITTERAPI_IO_KEY", "")
BASE = "https://api.twitterapi.io"


async def main() -> None:
    headers = {"x-api-key": API_KEY}
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"{BASE}/oapi/tweet_filter/get_rules", headers=headers)
        data = await r.json()
        print(json.dumps(data, indent=2))

        rules = data.get("rules") or []
        if len(rules) > 1:
            print(f"\nFound {len(rules)} rules — deleting all to start fresh...")
            for rule in rules:
                rid = rule.get("rule_id") or rule.get("id")
                dr = await s.delete(
                    f"{BASE}/oapi/tweet_filter/delete_rule",
                    headers=headers,
                    json={"rule_id": rid},
                )
                print(f"  Deleted {rid}: {dr.status}")
        else:
            print("\nOnly one rule, no cleanup needed.")


asyncio.run(main())
