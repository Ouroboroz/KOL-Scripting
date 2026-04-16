"""
python -m kol_data build    -- fetch items from GraphQL + prices from Pricegun
python -m kol_data prices   -- refresh prices only (graph cache must exist)
"""

import argparse
import logging
import sys
from pathlib import Path

from kol_data.cache import save_pickle, load_pickle
from kol_data.db.store import KolStore
from kol_data.sources.graphql import fetch_all_items
from kol_data.sources.npcstores import fetch_npc_stores
from kol_data.sources.pricegun import fetch_prices
from kol_data.graph.builder import build_graph
from kol_data.graph.queries import get_item, item_ids

DATA_DIR    = Path(__file__).parent / "data"
GRAPH_CACHE = DATA_DIR / "graph_cache.pkl"   # was graph_cache.json
DB_PATH     = DATA_DIR / "kol.duckdb"

GRAPH_TTL   = 24.0   # hours
PRICES_TTL  = 1.0    # hours


def cmd_build(force: bool) -> None:
    # ── Graph ──────────────────────────────────────────────────────────────────
    items = None if force else load_pickle(GRAPH_CACHE, GRAPH_TTL)
    if items is not None:
        print("Graph cache is fresh — use --force to rebuild")
    else:
        items = fetch_all_items()
        save_pickle(items, GRAPH_CACHE)

    G = build_graph(items)
    tradeable_ids = {iid for iid in item_ids(G) if get_item(G, iid).tradeable}

    # ── Prices ─────────────────────────────────────────────────────────────────
    # Check DuckDB for existing fresh prices before fetching
    prices_age: float | None = None
    with KolStore.open(DB_PATH) as store:
        fetched_at = store.prices_fetched_at()
        if fetched_at and not force:
            from datetime import datetime, timezone
            prices_age = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600

    if prices_age is not None and prices_age < PRICES_TTL:
        print(f"Prices are fresh ({prices_age:.1f}h old) — skipping price fetch")
        with KolStore.open(DB_PATH) as store:
            prices = store.load_current_prices()
    else:
        prices = fetch_prices(item_ids(G), tradeable_ids)
        with KolStore.open(DB_PATH) as store:
            store.upsert_items(items)
            store.upsert_prices(prices)

    # Ensure items are always written (idempotent), then fetch NPC store prices
    npc_rows = fetch_npc_stores()
    with KolStore.open(DB_PATH) as store:
        store.upsert_items(items)
        store.upsert_npc_prices(npc_rows)

    print(f"Items:            {len(item_ids(G)):,}")
    print(f"Edges:            {G.number_of_edges():,}")
    print(f"Items with price: {len(prices):,}")
    print(f"NPC store items:  {len(npc_rows):,}")
    print(f"DB:               {DB_PATH}")


def cmd_prices(force: bool) -> None:
    items = load_pickle(GRAPH_CACHE, ttl_hours=float("inf"))
    if items is None:
        print("No graph cache found — run `python -m kol_data build` first", file=sys.stderr)
        sys.exit(1)

    with KolStore.open(DB_PATH) as store:
        fetched_at = store.prices_fetched_at()

    if fetched_at and not force:
        from datetime import datetime, timezone
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        if age < PRICES_TTL:
            print(f"Prices are fresh ({age:.1f}h old) — use --force to refresh")
            return

    G = build_graph(items)
    tradeable_ids = {iid for iid in item_ids(G) if get_item(G, iid).tradeable}
    prices = fetch_prices(item_ids(G), tradeable_ids)
    npc_rows = fetch_npc_stores()

    with KolStore.open(DB_PATH) as store:
        store.upsert_prices(prices)
        store.upsert_npc_prices(npc_rows)

    no_data = len(tradeable_ids) - len(prices)
    print(f"Prices updated:  {len(prices):,}")
    print(f"No mall data:    {no_data:,}")
    print(f"NPC store items: {len(npc_rows):,}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(prog="python -m kol_data")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="Fetch items + prices").add_argument("--force", action="store_true")
    sub.add_parser("prices", help="Refresh prices only").add_argument("--force", action="store_true")

    args = parser.parse_args()
    if args.cmd == "build":
        cmd_build(args.force)
    elif args.cmd == "prices":
        cmd_prices(args.force)


if __name__ == "__main__":
    main()
