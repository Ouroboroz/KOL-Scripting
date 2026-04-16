"""Fetch NPC shop item/price data from kol-mafia's open data files."""
from __future__ import annotations

import logging
import httpx

log = logging.getLogger(__name__)

NPCSTORES_URL = (
    "https://raw.githubusercontent.com/kolmafia/kolmafia/main/src/data/npcstores.txt"
)

# Some stores appear multiple times under the same store_id but with different
# store names encoding the variant. Map store name → virtual store_id so we can
# gate access precisely. None means skip the entry entirely.
_STORE_NAME_REMAP: dict[str, str | None] = {
    "Hippy Store (Pre-War)": None,             # ignore — pre-war state
    "Hippy Store (Hippy)":   "hippy_hippy",    # hippy side won the war
    "Hippy Store (Fratboy)": "hippy_fratboy",  # fratboy side won the war
}


def fetch_npc_stores() -> list[tuple[str, str, int]]:
    """
    Fetch and parse npcstores.txt from kol-mafia's GitHub.

    Returns list of (store_id, item_name, price).

    Format (tab-separated): Store Name \\t store_id \\t item_name \\t price \\t ROW###
    Lines that are blank, start with '#', or have fewer than 4 fields are skipped.
    Store name variants (e.g. Hippy Store war outcomes) are remapped to virtual
    store_ids via _STORE_NAME_REMAP.
    """
    resp = httpx.get(NPCSTORES_URL, timeout=30)
    resp.raise_for_status()

    results: list[tuple[str, str, int]] = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        store_name = parts[0].strip()
        store_id = parts[1].strip()
        item_name = parts[2].strip()
        try:
            price = int(parts[3].strip())
        except ValueError:
            continue

        # Apply name-based remapping (variant stores, skip pre-war)
        if store_name in _STORE_NAME_REMAP:
            remapped = _STORE_NAME_REMAP[store_name]
            if remapped is None:
                continue
            store_id = remapped

        results.append((store_id, item_name, price))

    store_count = len({r[0] for r in results})
    log.info("Fetched %d NPC store entries across %d stores", len(results), store_count)
    return results
