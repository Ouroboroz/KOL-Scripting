"""
Shared dataclasses for kol_client.

All models are plain dataclasses (no Pydantic overhead needed here —
these are transient in-memory objects, never persisted).
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Inventory ─────────────────────────────────────────────────────────────────

@dataclass
class InventoryItem:
    item_id: int
    quantity: int
    name: str | None = None   # filled when name_lookup is provided


# ── Mall ──────────────────────────────────────────────────────────────────────

@dataclass
class MallListing:
    store_id: int
    store_name: str
    player_name: str
    item_id: int
    item_name: str | None
    unit_price: int
    quantity: int             # units currently in stock
    limit_per_day: int | None # None = no daily limit


@dataclass
class MallSearchResult:
    query: str                        # the original search string
    item_id: int | None               # resolved item ID if unambiguous
    item_name: str | None
    listings: list[MallListing]       # all listings, sorted cheapest-first
    pages_fetched: int


@dataclass
class BuyResult:
    success: bool
    item_id: int
    item_name: str | None
    quantity_bought: int
    total_spent: int          # in Meat
    message: str              # raw server response text for debugging


# ── NPC stores ────────────────────────────────────────────────────────────────

@dataclass
class NpcListing:
    item_id: int
    name: str | None
    price: int
    row_id: int | None = None     # whichrow value from shop HTML (required to buy)
    quantity: int | None = None   # None = unlimited / not shown


# ── Crafting ─────────────────────────────────────────────────────────────────

@dataclass
class CraftResult:
    success: bool
    item_id: int | None       # output item ID (None if server didn't report it)
    item_name: str | None
    quantity: int             # units produced
    message: str              # raw server response text for debugging
