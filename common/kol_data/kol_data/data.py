"""
Top-level KoL data container.

Keeps a clean boundary between two kinds of data:

  graph   — structural game data (items, concoctions, ingredients).
            Built from data.loathers.net. Changes rarely. Cached as a pickle.
            No price information. Never mutated after construction.

  prices  — market data (current mall prices, weekly history, individual sales).
            Sourced from pricegun.loathers.net and stored in DuckDB.
            Changes every hour. Passed explicitly where needed; never baked
            into graph node attributes.

PriceMode controls how prices are loaded:

  NONE    — don't load prices (e.g. --methods inspection, recipe browsing)
  CACHED  — load from DuckDB as-is; warn if stale, never fetch
  AUTO    — load from DuckDB; fetch fresh + store if TTL expired  (default for scan)
  FORCE   — always fetch fresh from pricegun regardless of TTL (build --force)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import networkx as nx

from kol_data.models.price import PriceData


class PriceMode(str, Enum):
    NONE   = "none"    # Skip prices entirely
    CACHED = "cached"  # DB only, warn if stale
    AUTO   = "auto"    # DB if fresh, fetch if stale
    FORCE  = "force"   # Always fetch from network


@dataclass
class KolData:
    """
    Immutable container for a loaded snapshot of KoL game + market data.

    graph               NetworkX DiGraph of items and recipes. Structural only —
                        no price attributes on nodes. Query with kol_data.graph.*
                        helpers (get_item, item_ids, find_item, etc.).

    prices              dict[item_id -> PriceData] from DuckDB current_prices +
                        price_history + sales tables. Empty if loaded with
                        PriceMode.NONE.

    prices_fetched_at   UTC datetime when prices were last written to DuckDB.
                        None when prices not loaded.
    """
    graph: nx.DiGraph
    prices: dict[int, PriceData] = field(default_factory=dict)
    npc_prices: dict[int, int] = field(default_factory=dict)  # item_id → cheapest accessible NPC price
    prices_fetched_at: datetime | None = None

    @property
    def has_prices(self) -> bool:
        return bool(self.prices)

    @property
    def prices_age_hours(self) -> float | None:
        if self.prices_fetched_at is None:
            return None
        return (datetime.now(timezone.utc) - self.prices_fetched_at).total_seconds() / 3600
