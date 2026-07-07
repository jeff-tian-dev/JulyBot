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
