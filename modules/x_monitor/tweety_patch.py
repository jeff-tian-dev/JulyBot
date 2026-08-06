"""Patch tweety transaction parsing for X's current webpack format.

Apply before any `from tweety import ...` usage. Safe to call multiple times.
"""
from __future__ import annotations

import re
from typing import Optional

import httpx

_PATCHED = False

ON_DEMAND_FILE_POINTER_REGEX = re.compile(
    r'(\d+)\s*:\s*"ondemand\.s"',
    flags=(re.VERBOSE | re.MULTILINE),
)
INDICES_REGEX = re.compile(
    r"""(\(\w{1}\[(\d{1,2})\],\s*16\))+""",
    flags=(re.VERBOSE | re.MULTILINE),
)


def _find_on_demand_file(text: str) -> Optional[str]:
    pointer_match = ON_DEMAND_FILE_POINTER_REGEX.search(text)
    if pointer_match is None:
        return None
    pointer = pointer_match.group(1)
    file = re.search(rf'{pointer}\s*:\s*"(\w+)"', text)
    return None if file is None else file.group(1)


def apply_tweety_patch() -> None:
    """Replace tweety TransactionGenerator.get_indices with a fixed implementation."""
    global _PATCHED
    if _PATCHED:
        return

    import tweety.transaction as tx
    import tweety.http as http

    async def _patched_init_local_api(self):
        # X stopped embedding the transaction-ID JS manifest in the *logged-out*
        # home page (as of ~2026-07-21), so tweety's default flow — which strips
        # cookies before fetching it — gets a stub page with no `ondemand.s`
        # pointer and get_indices raises "Couldn't get animation key indices".
        # Fetch the home page with cookies intact so the manifest is present;
        # only the guest-token call still needs to run unauthenticated.
        if not self._transaction:
            home_page_html = await self.get_home_html()
            self._transaction = tx.TransactionGenerator(home_page_html)

        if not self._guest_token:
            cookies = await self.remove_cookies()
            try:
                self._guest_token = await self._get_guest_token()
            finally:
                self.cookies = cookies

    http.Request._init_local_api = _patched_init_local_api

    def _patched_get_indices(self, home_page_html=None):
        key_byte_indices = []
        response = self.validate_response(home_page_html) or self.home_page_html
        on_demand_file = _find_on_demand_file(str(response))
        if on_demand_file:
            on_demand_file_url = (
                f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{on_demand_file}a.js"
            )
            on_demand_file_response = httpx.get(on_demand_file_url)
            key_byte_indices_match = INDICES_REGEX.finditer(str(on_demand_file_response.text))
            for item in key_byte_indices_match:
                key_byte_indices.append(item.group(2))
        if not key_byte_indices:
            raise Exception("Couldn't get animation key indices")
        key_byte_indices = list(map(int, key_byte_indices))
        return key_byte_indices[0], key_byte_indices[1:]

    tx.TransactionGenerator.get_indices = _patched_get_indices
    _PATCHED = True
