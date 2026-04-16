"""Shared graph + price loading logic — used by CLI and web UI."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx

from kol_data.cache import save_pickle, load_pickle
from kol_data.data import KolData, PriceMode
from kol_data.db.store import KolStore
from kol_data.models.item import Item
from kol_data.models.price import PriceData
from kol_data.sources.graphql import fetch_all_items
from kol_data.sources.pricegun import fetch_prices
from kol_data.graph.builder import build_graph
from kol_data.graph.queries import find_item as _find_item, find_items as _find_items, get_item, item_ids

from calculation.config import CraftingConfig

DATA_DIR    = Path(__file__).parent.parent.parent.parent / "common" / "kol_data" / "kol_data" / "data"
GRAPH_CACHE = DATA_DIR / "graph_cache.pkl"
DB_PATH     = DATA_DIR / "kol.duckdb"


def load_kol_data(
    config: CraftingConfig,
    price_mode: PriceMode = PriceMode.AUTO,
    force_graph: bool = False,
) -> KolData:
    """
    Load (or fetch) the item graph and optionally market prices.

    price_mode controls price loading:
      NONE    — graph only; prices dict is empty (use for --methods queries)
      CACHED  — load prices from DuckDB as-is; print warning if stale, never fetch
      AUTO    — load from DuckDB if fresh; fetch + store if TTL expired (default)
      FORCE   — always fetch fresh prices from pricegun
    """
    # ── Graph (pickle cache) ───────────────────────────────────────────────────
    graph_ttl = 0.0 if force_graph else config.graph_ttl_hours
    items: list[Item] | None = load_pickle(GRAPH_CACHE, graph_ttl)
    if items is None:
        logging.info("Graph cache missing or stale — fetching from GraphQL...")
        items = fetch_all_items()
        save_pickle(items, GRAPH_CACHE)

    G = build_graph(items)

    # ── Prices (DuckDB) ────────────────────────────────────────────────────────
    if price_mode == PriceMode.NONE:
        return KolData(graph=G)

    if not DB_PATH.exists():
        if price_mode == PriceMode.CACHED:
            print("WARNING: no price database found — run 'build' first", file=sys.stderr)
            return KolData(graph=G)
        # AUTO / FORCE — fetch and populate DB
        return _fetch_and_store(G, items, config)

    with KolStore.open(DB_PATH) as store:
        fetched_at = store.prices_fetched_at()

    if price_mode == PriceMode.FORCE or fetched_at is None:
        return _fetch_and_store(G, items, config)

    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600

    if price_mode == PriceMode.CACHED:
        if age_hours > config.prices_ttl_hours:
            print(
                f"WARNING: prices are {age_hours:.1f}h old "
                f"(TTL={config.prices_ttl_hours}h) — run 'build' to refresh",
                file=sys.stderr,
            )
        with KolStore.open(DB_PATH) as store:
            prices = store.load_current_prices()
            npc_prices = store.load_npc_prices(config.accessible_store_ids)
        return KolData(graph=G, prices=prices, npc_prices=npc_prices, prices_fetched_at=fetched_at)

    # AUTO: fetch if stale
    if age_hours > config.prices_ttl_hours:
        return _fetch_and_store(G, items, config)

    with KolStore.open(DB_PATH) as store:
        prices = store.load_current_prices()
        npc_prices = store.load_npc_prices(config.accessible_store_ids)
    return KolData(graph=G, prices=prices, npc_prices=npc_prices, prices_fetched_at=fetched_at)


def _fetch_and_store(G: nx.DiGraph, items: list[Item], config: CraftingConfig) -> KolData:
    """Fetch fresh prices from pricegun, persist to DuckDB, return KolData."""
    tradeable_ids = {iid for iid in item_ids(G) if get_item(G, iid).tradeable}
    prices = fetch_prices(item_ids(G), tradeable_ids)
    with KolStore.open(DB_PATH) as store:
        store.upsert_items(items)
        store.upsert_prices(prices)
        fetched_at = store.prices_fetched_at()
        npc_prices = store.load_npc_prices(config.accessible_store_ids)
    return KolData(graph=G, prices=prices, npc_prices=npc_prices, prices_fetched_at=fetched_at)


# ── Item lookup helpers (used by CLI + web UI) ────────────────────────────────

def find_item(G: nx.DiGraph, query: str) -> int | None:
    """Single-result lookup: exact name or unambiguous substring."""
    return _find_item(G, query)


def find_items_db(query: str) -> list[tuple[int, str]]:
    """
    Fast DuckDB text search — returns (id, name) pairs.
    Falls back to empty list if DB doesn't exist yet.
    """
    if not DB_PATH.exists():
        return []
    with KolStore.open(DB_PATH) as store:
        return store.find_items(query)


def find_items_graph(G: nx.DiGraph, query: str) -> list[tuple[int, str]]:
    """Graph-based multi-result search — fallback when DB not populated."""
    return _find_items(G, query)


def cache_ages() -> dict[str, float | None]:
    """Age in hours for graph cache and price DB, or None if missing."""
    from kol_data.cache import cache_age_hours
    prices_age: float | None = None
    if DB_PATH.exists():
        with KolStore.open(DB_PATH) as store:
            fetched_at = store.prices_fetched_at()
        if fetched_at:
            prices_age = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
    return {
        "graph": cache_age_hours(GRAPH_CACHE),
        "prices": prices_age,
    }
