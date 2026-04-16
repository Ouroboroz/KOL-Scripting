"""
DuckDB schema for KoL item, recipe, and price data.

All DDL is idempotent (CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS).
Call `create_schema(conn)` once after opening the database.

Note: DuckDB does not enforce foreign-key constraints; referential integrity is
maintained by the application layer (store.py).
"""

from __future__ import annotations

import duckdb


DDL = """
-- ── Items ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    tradeable   BOOLEAN NOT NULL DEFAULT FALSE,
    discardable BOOLEAN NOT NULL DEFAULT FALSE,
    autosell    INTEGER NOT NULL DEFAULT 0,
    uses        TEXT[]  NOT NULL DEFAULT []
);

-- ── Concoctions (recipes) ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS concoctions (
    id      INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL,
    methods TEXT[]  NOT NULL DEFAULT [],
    comment TEXT
);

CREATE INDEX IF NOT EXISTS concoctions_item_id ON concoctions(item_id);

-- Name search index (used by find_items text search)
CREATE INDEX IF NOT EXISTS items_name_lower ON items(lower(name));

-- ── Ingredients ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingredients (
    concoction_id INTEGER NOT NULL,
    item_id       INTEGER NOT NULL,
    quantity      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (concoction_id, item_id)
);

CREATE INDEX IF NOT EXISTS ingredients_item_id ON ingredients(item_id);

-- ── Current mall prices ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS current_prices (
    item_id       INTEGER PRIMARY KEY,
    mall_price    DOUBLE,
    mall_volume   INTEGER,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Price history (daily + weekly buckets) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_history (
    item_id    INTEGER NOT NULL,
    bucket_date DATE   NOT NULL,
    mode       VARCHAR NOT NULL DEFAULT 'weekly',  -- 'daily' or 'weekly'
    avg_price  DOUBLE  NOT NULL,
    volume     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (item_id, bucket_date, mode)
);

CREATE INDEX IF NOT EXISTS price_history_item ON price_history(item_id);

-- ── Individual sales ──────────────────────────────────────────────────────────
CREATE SEQUENCE IF NOT EXISTS sales_id_seq;
CREATE TABLE IF NOT EXISTS sales (
    id         BIGINT  PRIMARY KEY DEFAULT nextval('sales_id_seq'),
    item_id    INTEGER NOT NULL,
    sold_at    TIMESTAMPTZ NOT NULL,
    unit_price DOUBLE,
    quantity   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS sales_item_id ON sales(item_id);
CREATE INDEX IF NOT EXISTS sales_sold_at  ON sales(sold_at);

-- ── NPC shop prices ───────────────────────────────────────────────────────────
-- Sourced from kol-mafia's npcstores.txt. Refreshed on each `build`.
-- store_id matches the identifier in npcstores.txt (e.g. "jewelers" for
-- Little Canadia Jewelers). Access is gated by moon sign in CraftingConfig.
CREATE TABLE IF NOT EXISTS npc_prices (
    item_id  INTEGER NOT NULL,
    store_id TEXT    NOT NULL,
    price    INTEGER NOT NULL,
    PRIMARY KEY (item_id, store_id)
);

-- ── Live mall snapshots ────────────────────────────────────────────────────────
-- Written after each verification run. Tracks cheapest ask + real margin over
-- time so we can chart how live prices drift vs Pricegun rolling averages.
-- captured_at is truncated to the minute so back-to-back runs don't pile up.
CREATE TABLE IF NOT EXISTS mall_snapshots (
    item_id         INTEGER     NOT NULL,
    captured_at     TIMESTAMPTZ NOT NULL,
    cheapest_ask    INTEGER     NOT NULL,   -- raw cheapest listing price
    listings_count  INTEGER,
    real_craft_cost DOUBLE,                 -- live ingredient cost + overhead
    real_profit     DOUBLE,                 -- cheapest_ask - 1 - real_craft_cost
    PRIMARY KEY (item_id, captured_at)
);

CREATE INDEX IF NOT EXISTS mall_snapshots_item ON mall_snapshots(item_id);
CREATE INDEX IF NOT EXISTS mall_snapshots_time ON mall_snapshots(captured_at);
"""


def create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables and indexes if they don't already exist."""
    conn.execute(DDL)
