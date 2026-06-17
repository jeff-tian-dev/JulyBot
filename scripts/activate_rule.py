"""One-off: test activate a filter rule via twitterapi.io API."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import aiohttp

API_KEY = os.environ.get("TWITTERAPI_IO_KEY", "")
BASE = "https://api.twitterapi.io"


async def main() -> None:
    headers = {"x-api-key": API_KEY}
    async with aiohttp.ClientSession() as s:
        # Get current rules
        r = await s.get(f"{BASE}/oapi/tweet_filter/get_rules", headers=headers)
        data = await r.json()
        rules = data.get("rules") or []
        print(f"Current rules ({len(rules)}):")
        for rule in rules:
            print(f"  {rule['rule_id']} is_effect={rule['is_effect']} tag={rule['tag']}")

        if not rules:
            print("No rules found.")
            return

        rule = rules[0]
        rid = rule["rule_id"]
        print(f"\nTrying to activate rule {rid} with is_effect=1 ...")
        body = {
            "rule_id": rid,
            "tag": rule["tag"],
            "value": rule["value"],
            "interval_seconds": rule["interval_seconds"],
            "is_effect": 1,
        }
        r2 = await s.post(f"{BASE}/oapi/tweet_filter/update_rule", headers=headers, json=body)
        resp = await r2.json()
        print(f"HTTP {r2.status}: {json.dumps(resp, indent=2)}")

        print("\nTrying is_effect=True (bool) ...")
        body2 = dict(body, is_effect=True)
        r3 = await s.post(f"{BASE}/oapi/tweet_filter/update_rule", headers=headers, json=body2)
        resp3 = await r3.json()
        print(f"HTTP {r3.status}: {json.dumps(resp3, indent=2)}")

        # Final state
        r4 = await s.get(f"{BASE}/oapi/tweet_filter/get_rules", headers=headers)
        data4 = await r4.json()
        for rule in (data4.get("rules") or []):
            print(f"\nFinal: {rule['rule_id']} is_effect={rule['is_effect']}")


asyncio.run(main())
