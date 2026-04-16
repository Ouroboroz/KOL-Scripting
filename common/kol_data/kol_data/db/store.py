"""
DuckDB upsert and query helpers for KoL data.

Bulk insert pattern: write into a temp table (no conflict checks, fast), then
do a single INSERT ... SELECT ... ON CONFLICT from temp → permanent table.
This avoids the per-row overhead of executemany with ON CONFLICT.

Usage:
    with KolStore.open("kol.duckdb") as store:
        store.upsert_items(items)
        store.upsert_prices(prices)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb

from kol_data.db.schema import create_schema
from kol_data.models.item import Item
from kol_data.models.price import PriceData


class KolStore:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @classmethod
    def open(cls, path: str | Path) -> KolStore:
        conn = duckdb.connect(str(path))
        create_schema(conn)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> KolStore:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _bulk_upsert(
        self,
        tmp_name: str,
        tmp_ddl: str,
        rows: list[tuple],
        insert_sql: str,
        upsert_sql: str,
    ) -> None:
        """
        Create a temp table, bulk-load rows, upsert into permanent table, drop temp.
        All within a single transaction for speed.
        """
        conn = self._conn
        conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS {tmp_name} ({tmp_ddl})")
        conn.execute(f"DELETE FROM {tmp_name}")
        conn.executemany(insert_sql, rows)
        conn.execute(upsert_sql)
        conn.execute(f"DROP TABLE {tmp_name}")

    # ── Items + recipes ────────────────────────────────────────────────────────

    def upsert_items(self, items: list[Item]) -> None:
        """Bulk-upsert items, concoctions, and ingredients."""
        conn = self._conn

        item_rows = [
            (i.id, i.name, i.tradeable, i.discardable, i.autosell, i.uses)
            for i in items
        ]
        concoction_rows = [
            (c.id, c.item_id, c.methods, c.comment)
            for i in items for c in i.concoctions
        ]
        ingredient_rows = [
            (c.id, ing.item_id, ing.quantity)
            for i in items for c in i.concoctions for ing in c.ingredients
        ]

        conn.execute("BEGIN")
        try:
            # Items
            conn.execute("""
                CREATE TEMP TABLE IF NOT EXISTS _items_tmp (
                    id INTEGER, name TEXT, tradeable BOOLEAN,
                    discardable BOOLEAN, autosell INTEGER, uses TEXT[]
                )
            """)
            conn.execute("DELETE FROM _items_tmp")
            conn.executemany(
                "INSERT INTO _items_tmp VALUES (?, ?, ?, ?, ?, ?)", item_rows
            )
            conn.execute("""
                INSERT INTO items
                SELECT * FROM _items_tmp
                ON CONFLICT (id) DO UPDATE SET
                    name=excluded.name, tradeable=excluded.tradeable,
                    discardable=excluded.discardable, autosell=excluded.autosell,
                    uses=excluded.uses
            """)
            conn.execute("DROP TABLE _items_tmp")

            # Concoctions
            if concoction_rows:
                conn.execute("""
                    CREATE TEMP TABLE IF NOT EXISTS _concoctions_tmp (
                        id INTEGER, item_id INTEGER, methods TEXT[], comment TEXT
                    )
                """)
                conn.execute("DELETE FROM _concoctions_tmp")
                conn.executemany(
                    "INSERT INTO _concoctions_tmp VALUES (?, ?, ?, ?)", concoction_rows
                )
                conn.execute("""
                    INSERT INTO concoctions
                    SELECT * FROM _concoctions_tmp
                    ON CONFLICT (id) DO UPDATE SET
                        item_id=excluded.item_id, methods=excluded.methods,
                        comment=excluded.comment
                """)
                conn.execute("DROP TABLE _concoctions_tmp")

            # Ingredients
            if ingredient_rows:
                conn.execute("""
                    CREATE TEMP TABLE IF NOT EXISTS _ingredients_tmp (
                        concoction_id INTEGER, item_id INTEGER, quantity INTEGER
                    )
                """)
                conn.execute("DELETE FROM _ingredients_tmp")
                conn.executemany(
                    "INSERT INTO _ingredients_tmp VALUES (?, ?, ?)", ingredient_rows
                )
                conn.execute("""
                    INSERT INTO ingredients
                    SELECT * FROM _ingredients_tmp
                    ON CONFLICT (concoction_id, item_id) DO UPDATE SET
                        quantity=excluded.quantity
                """)
                conn.execute("DROP TABLE _ingredients_tmp")

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ── Prices ─────────────────────────────────────────────────────────────────

    def upsert_prices(self, prices: dict[int, PriceData]) -> None:
        """Bulk-upsert current prices, append new weekly history and sales."""
        now = datetime.now(timezone.utc)
        conn = self._conn

        current_rows = [
            (item_id, p.latest_price(), p.volume, now)
            for item_id, p in prices.items()
        ]
        history_rows = [
            (item_id, bucket.date, "daily", bucket.price, bucket.volume)
            for item_id, p in prices.items()
            for bucket in p.history_daily
        ] + [
            (item_id, bucket.date, "weekly", bucket.price, bucket.volume)
            for item_id, p in prices.items()
            for bucket in p.history_weekly
        ]
        sale_rows = [
            (item_id, sale.date, sale.unit_price, sale.quantity)
            for item_id, p in prices.items()
            for sale in p.sales
            if sale.unit_price is not None
        ]

        conn.execute("BEGIN")
        try:
            # Current prices
            conn.execute("""
                CREATE TEMP TABLE IF NOT EXISTS _current_tmp (
                    item_id INTEGER, mall_price DOUBLE,
                    mall_volume INTEGER, fetched_at TIMESTAMPTZ
                )
            """)
            conn.execute("DELETE FROM _current_tmp")
            conn.executemany("INSERT INTO _current_tmp VALUES (?, ?, ?, ?)", current_rows)
            conn.execute("""
                INSERT INTO current_prices
                SELECT * FROM _current_tmp
                ON CONFLICT (item_id) DO UPDATE SET
                    mall_price=excluded.mall_price,
                    mall_volume=excluded.mall_volume,
                    fetched_at=excluded.fetched_at
            """)
            conn.execute("DROP TABLE _current_tmp")

            # Price history
            if history_rows:
                conn.execute("""
                    CREATE TEMP TABLE IF NOT EXISTS _history_tmp (
                        item_id INTEGER, bucket_date DATE,
                        mode VARCHAR, avg_price DOUBLE, volume INTEGER
                    )
                """)
                conn.execute("DELETE FROM _history_tmp")
                conn.executemany(
                    "INSERT INTO _history_tmp VALUES (?, ?, ?, ?, ?)", history_rows
                )
                conn.execute("""
                    INSERT INTO price_history
                    SELECT * FROM _history_tmp
                    ON CONFLICT (item_id, bucket_date, mode) DO NOTHING
                """)
                conn.execute("DROP TABLE _history_tmp")

            # Sales — bulk load into temp, then anti-join insert
            if sale_rows:
                conn.execute("""
                    CREATE TEMP TABLE IF NOT EXISTS _sales_tmp (
                        item_id INTEGER, sold_at TIMESTAMPTZ,
                        unit_price DOUBLE, quantity INTEGER
                    )
                """)
                conn.execute("DELETE FROM _sales_tmp")
                conn.executemany(
                    "INSERT INTO _sales_tmp VALUES (?, ?, ?, ?)", sale_rows
                )
                conn.execute("""
                    INSERT INTO sales (item_id, sold_at, unit_price, quantity)
                    SELECT s.item_id, s.sold_at, s.unit_price, s.quantity
                    FROM _sales_tmp s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM sales t
                        WHERE t.item_id    = s.item_id
                          AND t.sold_at    = s.sold_at
                          AND t.unit_price = s.unit_price
                          AND t.quantity   = s.quantity
                    )
                """)
                conn.execute("DROP TABLE _sales_tmp")

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_price_history(self, item_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT week_start, avg_price, volume FROM price_history"
            " WHERE item_id = ? ORDER BY week_start",
            [item_id],
        ).fetchall()
        return [{"date": r[0], "price": r[1], "volume": r[2]} for r in rows]

    def get_recent_sales(self, item_id: int, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT sold_at, unit_price, quantity FROM sales"
            " WHERE item_id = ? ORDER BY sold_at DESC LIMIT ?",
            [item_id, limit],
        ).fetchall()
        return [{"date": r[0], "unit_price": r[1], "quantity": r[2]} for r in rows]

    def get_current_price(self, item_id: int) -> float | None:
        row = self._conn.execute(
            "SELECT mall_price FROM current_prices WHERE item_id = ?", [item_id]
        ).fetchone()
        return row[0] if row else None

    def tradeable_item_ids(self) -> list[int]:
        return [
            r[0] for r in self._conn.execute(
                "SELECT id FROM items WHERE tradeable = TRUE"
            ).fetchall()
        ]

    def prune_old_sales(self, keep_days: int = 30) -> int:
        result = self._conn.execute(
            f"DELETE FROM sales WHERE sold_at < now() - INTERVAL '{keep_days} days'"
        )
        return result.rowcount

    # ── NPC prices ────────────────────────────────────────────────────────────

    def upsert_npc_prices(self, rows: list[tuple[str, str, int]]) -> None:
        """
        rows: list of (store_id, item_name, price) from npcstores.txt.
        Resolves item_name → item_id via the items table; unrecognised names skipped.
        """
        if not rows:
            return
        conn = self._conn
        name_map: dict[str, int] = {
            name.lower(): item_id
            for item_id, name in conn.execute("SELECT id, name FROM items").fetchall()
        }
        resolved = [
            (name_map[item_name.lower()], store_id, price)
            for store_id, item_name, price in rows
            if item_name.lower() in name_map
        ]
        if not resolved:
            return
        conn.execute("BEGIN")
        try:
            conn.execute("""
                CREATE TEMP TABLE IF NOT EXISTS _npc_tmp (
                    item_id INTEGER, store_id TEXT, price INTEGER
                )
            """)
            conn.execute("DELETE FROM _npc_tmp")
            conn.executemany("INSERT INTO _npc_tmp VALUES (?, ?, ?)", resolved)
            conn.execute("""
                INSERT INTO npc_prices
                SELECT * FROM _npc_tmp
                ON CONFLICT (item_id, store_id) DO UPDATE SET price=excluded.price
            """)
            conn.execute("DROP TABLE _npc_tmp")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def load_npc_prices(self, store_ids: set[str]) -> dict[int, int]:
        """
        Return dict[item_id → min_price] filtered to the given accessible store_ids.
        Returns empty dict if store_ids is empty (no stores accessible).
        """
        if not store_ids:
            return {}
        placeholders = ", ".join("?" * len(store_ids))
        rows = self._conn.execute(
            f"SELECT item_id, MIN(price) FROM npc_prices"
            f" WHERE store_id IN ({placeholders}) GROUP BY item_id",
            list(store_ids),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    # ── Mall snapshots ─────────────────────────────────────────────────────────

    def upsert_mall_snapshots(self, rows: list[tuple]) -> None:
        """
        Persist live-verification snapshots.

        Each row: (item_id, captured_at, cheapest_ask, listings_count,
                   real_craft_cost, real_profit)
        captured_at should be truncated to the minute by the caller so that
        repeated runs within a minute overwrite rather than accumulate.
        """
        if not rows:
            return
        conn = self._conn
        conn.execute("BEGIN")
        try:
            conn.execute("""
                CREATE TEMP TABLE IF NOT EXISTS _snap_tmp (
                    item_id INTEGER, captured_at TIMESTAMPTZ,
                    cheapest_ask INTEGER, listings_count INTEGER,
                    real_craft_cost DOUBLE, real_profit DOUBLE
                )
            """)
            conn.execute("DELETE FROM _snap_tmp")
            conn.executemany("INSERT INTO _snap_tmp VALUES (?, ?, ?, ?, ?, ?)", rows)
            conn.execute("""
                INSERT INTO mall_snapshots
                SELECT * FROM _snap_tmp
                ON CONFLICT (item_id, captured_at) DO UPDATE SET
                    cheapest_ask=excluded.cheapest_ask,
                    listings_count=excluded.listings_count,
                    real_craft_cost=excluded.real_craft_cost,
                    real_profit=excluded.real_profit
            """)
            conn.execute("DROP TABLE _snap_tmp")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def load_mall_snapshots(self, item_id: int) -> list[dict]:
        """
        Return all recorded live-price snapshots for one item, oldest first.
        Each dict has keys: captured_at, cheapest_ask, listings_count,
        real_craft_cost, real_profit.
        """
        rows = self._conn.execute("""
            SELECT captured_at, cheapest_ask, listings_count,
                   real_craft_cost, real_profit
            FROM mall_snapshots
            WHERE item_id = ?
            ORDER BY captured_at ASC
        """, [item_id]).fetchall()
        return [
            {
                "captured_at":    r[0],
                "cheapest_ask":   r[1],
                "listings_count": r[2],
                "real_craft_cost": r[3],
                "real_profit":    r[4],
            }
            for r in rows
        ]

    # ── Text search ────────────────────────────────────────────────────────────

    def find_items(self, query: str) -> list[tuple[int, str]]:
        """Return (id, name) pairs whose name contains query (case-insensitive)."""
        rows = self._conn.execute(
            "SELECT id, name FROM items WHERE lower(name) LIKE ? ORDER BY name",
            [f"%{query.lower()}%"],
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    # ── Price loading ──────────────────────────────────────────────────────────

    def prices_fetched_at(self) -> datetime | None:
        """UTC datetime of the most recent price upsert, or None if no prices stored."""
        row = self._conn.execute(
            "SELECT MAX(fetched_at) FROM current_prices"
        ).fetchone()
        return row[0] if row and row[0] else None

    def load_current_prices(self) -> dict[int, PriceData]:
        """
        Reconstruct dict[item_id -> PriceData] from DuckDB.
        Includes current price, full weekly history, and individual sales.
        """
        from collections import defaultdict
        from kol_data.models.price import PriceHistoryBucket, Sale

        price_rows = self._conn.execute("""
            SELECT cp.item_id, cp.mall_price, cp.mall_volume, i.name
            FROM current_prices cp
            LEFT JOIN items i ON cp.item_id = i.id
        """).fetchall()

        result: dict[int, PriceData] = {}
        for item_id, mall_price, mall_volume, name in price_rows:
            result[item_id] = PriceData(
                item_id=item_id,
                name=name or "",
                current_price=mall_price,
                volume=mall_volume or 0,
            )

        history_rows = self._conn.execute("""
            SELECT item_id, bucket_date, mode, avg_price, volume
            FROM price_history ORDER BY item_id, mode, bucket_date
        """).fetchall()

        history_daily_by_item: dict[int, list] = defaultdict(list)
        history_weekly_by_item: dict[int, list] = defaultdict(list)
        for item_id, bucket_date, mode, avg_price, volume in history_rows:
            if hasattr(bucket_date, "year"):
                dt = datetime(bucket_date.year, bucket_date.month, bucket_date.day, tzinfo=timezone.utc)
            else:
                dt = bucket_date
            bucket = PriceHistoryBucket(item_id=item_id, date=dt, price=avg_price, volume=volume or 0)
            if mode == "daily":
                history_daily_by_item[item_id].append(bucket)
            else:
                history_weekly_by_item[item_id].append(bucket)
        for item_id in result:
            result[item_id].history_daily = history_daily_by_item.get(item_id, [])
            result[item_id].history_weekly = history_weekly_by_item.get(item_id, [])

        sales_rows = self._conn.execute("""
            SELECT item_id, sold_at, unit_price, quantity
            FROM sales ORDER BY item_id, sold_at DESC
        """).fetchall()

        sales_by_item: dict[int, list] = defaultdict(list)
        for item_id, sold_at, unit_price, quantity in sales_rows:
            sales_by_item[item_id].append(
                Sale(date=sold_at, unit_price=unit_price, quantity=quantity or 1)
            )
        for item_id, sales_list in sales_by_item.items():
            if item_id in result:
                result[item_id].sales = sales_list

        return result
