"""
Inventory containers for Kingdom of Loathing.

Class hierarchy
───────────────
    Inventory (abstract base)
    ├── MainInventory   — main pack             (api.php?what=inventory)
    ├── Closet          — closet storage        (api.php?what=closet)
    ├── Storage         — Hagnk's storage       (api.php?what=storage)
    └── Equipment       — currently worn items  (api.php?what=equipment)

All four parse the same ``{ "<item_id>": <qty>, ... }`` JSON structure that
KoL's api.php returns, so the base class handles all the heavy lifting.
Equipment is technically slot-based on the game side but KoL's api.php still
returns it as a plain id→qty dict, so it fits the same base class without
any special overrides.

Usage
─────
    inv = MainInventory()
    inv.refresh(session)                     # fetches from KoL
    inv.refresh(session, name_lookup={...})  # also fills item names

    inv.quantity(9360)          # → 3
    9360 in inv                 # → True
    inv[9360]                   # → InventoryItem(item_id=9360, quantity=3, name='Fish Head')
    inv.items                   # → list[InventoryItem], sorted by item_id

    # Compare two containers
    in_closet_not_inv = set(closet.item_ids()) - set(inv.item_ids())
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from kol_session.session import KoLSession
from kol_client.models import InventoryItem

log = logging.getLogger(__name__)


class Inventory(ABC):
    """Abstract base class for a KoL inventory container.

    Subclasses only need to declare ``_WHAT`` (the ``api.php?what=`` value)
    and ``_KEY`` (the top-level JSON key that holds the item dict).
    In practice these are the same string, but Equipment differs slightly.
    """

    # Subclasses override these two class-level constants
    _WHAT: str   # query param value for api.php?what=
    _KEY: str    # top-level key in the JSON response

    def __init__(self) -> None:
        self._items: dict[int, InventoryItem] = {}   # item_id → InventoryItem

    # ── Data access ───────────────────────────────────────────────────────────

    @property
    def items(self) -> list[InventoryItem]:
        """All items, sorted by item_id."""
        return sorted(self._items.values(), key=lambda x: x.item_id)

    def quantity(self, item_id: int) -> int:
        """Return quantity of an item, or 0 if not present."""
        entry = self._items.get(item_id)
        return entry.quantity if entry else 0

    def item_ids(self) -> list[int]:
        """Sorted list of item IDs currently in this container."""
        return sorted(self._items.keys())

    def __contains__(self, item_id: int) -> bool:
        return item_id in self._items

    def __getitem__(self, item_id: int) -> InventoryItem:
        if item_id not in self._items:
            raise KeyError(f"item_id {item_id} not in {type(self).__name__}")
        return self._items[item_id]

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({len(self)} items)"

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(
        self,
        session: KoLSession,
        name_lookup: dict[int, str] | None = None,
    ) -> None:
        """Fetch current contents from KoL and replace local state.

        Args:
            session: An authenticated KoLSession.
            name_lookup: Optional dict[item_id → name] built from kol_data.
                         When provided, InventoryItem.name is filled in.
        """
        resp = session.get("api.php", params={"what": self._WHAT, "for": "kol_client"})
        resp.raise_for_status()

        data = resp.json()
        raw: dict[str, int] = {item_id:int(data[item_id]) for item_id in data}

        self._items = {}
        for id_str, qty in raw.items():
            try:
                item_id = int(id_str)
            except ValueError:
                log.debug("Skipping non-integer item_id key: %r", id_str)
                continue

            name = name_lookup.get(item_id) if name_lookup else None
            self._items[item_id] = InventoryItem(
                item_id=item_id,
                quantity=int(qty),
                name=name,
            )

        log.info(
            "%s refreshed: %d unique items",
            type(self).__name__,
            len(self._items),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def enrich_names(self, name_lookup: dict[int, str]) -> None:
        """Back-fill item names from a lookup dict (in-place, no network call)."""
        for item_id, entry in self._items.items():
            if entry.name is None and item_id in name_lookup:
                entry.name = name_lookup[item_id]


# ── Concrete containers ───────────────────────────────────────────────────────

class MainInventory(Inventory):
    """The player's main pack — items they're currently carrying."""
    _WHAT = "inventory"


class Closet(Inventory):
    """The closet — items stored in Meat and item form."""
    _WHAT = "closet"


class Storage(Inventory):
    """Hagnk's Ancestral Mini-Storage — accessible in Ronin/Hardcore."""
    _WHAT = "storage"


class Equipment(Inventory):
    """Currently equipped items (hat, weapon, off-hand, pants, accessory slots, etc.)."""
    _WHAT = "equipment"
