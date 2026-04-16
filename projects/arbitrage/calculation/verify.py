"""
Depth verification — second pass on scan results using live mall order books.

After scan_profitable() finds candidates using Pricegun rolling averages, this
module queries the actual current order book for each candidate's output item
and its ingredients to confirm:

  1. The sell price is real (cheapest current listing - 1 Meat).
  2. The ingredients can actually be purchased at the assumed quantities and price.

Usage
─────
    from kol_session.session import KoLSession
    from calculation.verify import verify_top_results, print_verified_results

    with KoLSession.from_env() as session:
        verified = verify_top_results(
            session=session,
            results=scan_results,       # list[ScanResult] from scan_profitable()
            graph=kol.graph,
            prices=kol.prices,
            config=config,
            npc_prices=kol.npc_prices,
            top_n=20,
            units=10,                   # verify depth for 10-unit batches
            request_delay=3.0,
        )
    print_verified_results(verified)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx

from kol_session.session import KoLSession
from kol_client.mall import search_mall, buy_depth
from kol_data.models.price import PriceData
from kol_data.graph.queries import get_item

from calculation.config import CraftingConfig
from calculation.cost import compute_crafting_cost, _buy_cost, _price_source

log = logging.getLogger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────

@dataclass
class IngredientDepth:
    item_id: int
    item_name: str | None
    qty_per_craft: int           # units needed per output unit crafted
    avg_price: float             # weighted avg cost/unit from the real order book
    cached_price: float          # price used by the original scan (Pricegun)
    can_fill: bool               # order book can supply qty_per_craft * units_verified
    source: str                  # "mall", "npc", "autosell_fallback"


@dataclass
class VerifiedScanResult:
    """A ScanResult enriched with live order-book data."""

    # ── Original scan data ───────────────────────────────────────────────────
    item_id: int
    item_name: str
    cached_craft_cost: float
    cached_sell_price: float
    cached_profit: float
    cached_margin_pct: float

    # ── Real prices from order book ──────────────────────────────────────────
    real_sell_price: float | None    # cheapest_ask - 1 (what we'd list at)
    real_craft_cost: float | None    # ingredient costs at real prices + overhead
    real_profit: float | None
    real_margin_pct: float | None

    # ── Depth verdict ────────────────────────────────────────────────────────
    depth_ok: bool                   # True = positive real profit + all inputs fillable
    sell_depth_ok: bool              # output item has active listings
    input_depth_ok: bool             # all mall inputs can be filled
    units_verified: int              # batch size used for depth check

    # ── Per-ingredient breakdown ─────────────────────────────────────────────
    ingredients: list[IngredientDepth] = field(default_factory=list)

    # ── Error (set when a network call fails) ────────────────────────────────
    error: str | None = None

    @property
    def price_delta_pct(self) -> float | None:
        """How much the real sell price differs from the cached Pricegun price (%)."""
        if self.real_sell_price is None or self.cached_sell_price == 0:
            return None
        return ((self.real_sell_price - self.cached_sell_price) / self.cached_sell_price) * 100


# ── Ingredient collection ─────────────────────────────────────────────────────

def _collect_purchased_ingredients(
    graph: nx.DiGraph,
    prices: dict[int, PriceData],
    item_id: int,
    config: CraftingConfig,
    npc_prices: dict[int, int] | None,
    qty: int = 1,
    visited: frozenset[int] | None = None,
) -> dict[int, tuple[int, float, str]]:
    """
    Recursively collect all directly-purchased ingredients in the crafting tree.

    Returns a dict of ``{item_id: (total_qty_per_output_unit, cached_price, source)}``.
    Crafted intermediates are expanded into their own purchased leaves.
    Quantities are correctly scaled at each level.

    Args:
        qty: How many units of item_id are needed by the parent recipe.
    """
    if visited is None:
        visited = frozenset({item_id})

    result = compute_crafting_cost(graph, prices, item_id, config, npc_prices)

    # No recipe, or cheaper to buy: this item is a leaf — it's purchased.
    if (
        result.unavailable
        or result.total_cost is None
        or (result.buy_cost is not None and result.total_cost >= result.buy_cost)
    ):
        buy_price = _buy_cost(graph, prices, item_id, npc_prices)
        if buy_price is None:
            return {}
        source = _price_source(prices.get(item_id), item_id, npc_prices)
        return {item_id: (qty, buy_price, source)}

    # Cheaper to craft: recurse into the winning concoction's ingredients.
    if result.chosen_concoction_id is None:
        return {}

    item = get_item(graph, item_id)
    chosen_conc = next(
        (c for c in item.concoctions if c.id == result.chosen_concoction_id),
        None,
    )
    if chosen_conc is None:
        return {}

    purchased: dict[int, tuple[int, float, str]] = {}
    child_visited = visited | frozenset({item_id})

    for ing in chosen_conc.ingredients:
        if ing.item_id in visited:
            continue
        sub = _collect_purchased_ingredients(
            graph, prices, ing.item_id, config, npc_prices,
            qty=qty * ing.quantity,
            visited=child_visited,
        )
        for iid, (sub_qty, sub_cost, sub_src) in sub.items():
            if iid in purchased:
                existing_qty, existing_cost, existing_src = purchased[iid]
                purchased[iid] = (existing_qty + sub_qty, existing_cost, existing_src)
            else:
                purchased[iid] = (sub_qty, sub_cost, sub_src)

    return purchased


# ── Main verification pass ────────────────────────────────────────────────────

def verify_top_results(
    session: KoLSession,
    results: list,                      # list[ScanResult] — avoid circular import
    graph: nx.DiGraph,
    prices: dict[int, PriceData],
    config: CraftingConfig,
    npc_prices: dict[int, int] | None = None,
    top_n: int = 20,
    units: int = 10,
    request_delay: float = 3.0,
    name_lookup: dict[int, str] | None = None,
) -> list[VerifiedScanResult]:
    """Verify the top N scan results using live mall order books.

    For each candidate:
      1. Search mall for the output item → real_sell_price = cheapest_ask - 1.
      2. Recursively collect purchased ingredients for the winning recipe.
      3. Search mall for each mall-sourced ingredient → real buy cost via buy_depth().
      4. Recompute craft cost and profit at real prices.

    Args:
        session:       Authenticated KoLSession.
        results:       Output of scan_profitable(), sorted by net_score.
        graph:         NetworkX item/recipe graph.
        prices:        Pricegun price data (used to resolve recipe choices).
        config:        Player crafting config.
        npc_prices:    NPC store prices dict.
        top_n:         How many candidates to verify.
        units:         Batch size for depth check (e.g. 10 → check depth for 10 crafts).
        request_delay: Seconds to sleep between mall requests.
        name_lookup:   Optional dict[item_id → name] for display enrichment.

    Returns:
        list[VerifiedScanResult] in the same order as results[:top_n].
    """
    candidates = results[:top_n]
    verified: list[VerifiedScanResult] = []

    for i, result in enumerate(candidates):
        log.info(
            "Verifying %d/%d: %s (#%d)",
            i + 1, len(candidates), result.item_name, result.item_id,
        )
        vr = _verify_one(
            session=session,
            result=result,
            graph=graph,
            prices=prices,
            config=config,
            npc_prices=npc_prices,
            units=units,
            request_delay=request_delay,
            name_lookup=name_lookup,
        )
        verified.append(vr)

    return verified


def _verify_one(
    session: KoLSession,
    result,                     # ScanResult
    graph: nx.DiGraph,
    prices: dict[int, PriceData],
    config: CraftingConfig,
    npc_prices: dict[int, int] | None,
    units: int,
    request_delay: float,
    name_lookup: dict[int, str] | None,
) -> VerifiedScanResult:
    base = dict(
        item_id=result.item_id,
        item_name=result.item_name,
        cached_craft_cost=result.craft_cost,
        cached_sell_price=result.mall_price,
        cached_profit=result.profit,
        cached_margin_pct=result.margin_pct,
        units_verified=units,
    )

    # ── 1. Real sell price ────────────────────────────────────────────────────
    # pudnuggler is a name search — use the item name, not its numeric ID.
    output_name = get_item(graph, result.item_id).name
    time.sleep(request_delay)
    try:
        sell_search = search_mall(session, output_name, max_pages=1)
    except Exception as exc:
        return VerifiedScanResult(
            **base,
            real_sell_price=None, real_craft_cost=None,
            real_profit=None, real_margin_pct=None,
            depth_ok=False, sell_depth_ok=False, input_depth_ok=False,
            error=f"Output item search failed: {exc}",
        )

    # Filter to the expected item in case the name search returns multiple items.
    sell_listings = [l for l in sell_search.listings
                     if l.item_id == 0 or l.item_id == result.item_id]
    if not sell_listings:
        sell_listings = sell_search.listings  # fall back to all if item_id parsing failed

    if not sell_listings:
        return VerifiedScanResult(
            **base,
            real_sell_price=None, real_craft_cost=None,
            real_profit=None, real_margin_pct=None,
            depth_ok=False, sell_depth_ok=False, input_depth_ok=False,
            error="No mall listings for output item",
        )

    sell_depth_ok = True
    real_sell_price = float(sell_listings[0].unit_price - 1)

    # ── 2. Collect purchased ingredients ─────────────────────────────────────
    purchased = _collect_purchased_ingredients(
        graph, prices, result.item_id, config, npc_prices
    )

    if not purchased:
        return VerifiedScanResult(
            **base,
            real_sell_price=real_sell_price, real_craft_cost=None,
            real_profit=None, real_margin_pct=None,
            depth_ok=False, sell_depth_ok=sell_depth_ok, input_depth_ok=False,
            error="Could not resolve ingredient tree",
        )

    # ── 3. Query order books for mall-sourced ingredients ─────────────────────
    ingredient_depths: list[IngredientDepth] = []
    real_ingredient_cost = 0.0
    input_depth_ok = True

    # Re-run cost calculation to get per-craft overhead from the winning recipe
    cost_result = compute_crafting_cost(graph, prices, result.item_id, config, npc_prices)
    overhead = sum(
        step.unit_cost for step in cost_result.breakdown
        if step.source == "overhead"
    )

    for ing_id, (qty_per_craft, cached_price, source) in purchased.items():
        ing_name = (name_lookup.get(ing_id) if name_lookup else None)

        if source != "mall":
            # NPC / autosell: fixed price, no depth check needed
            real_ingredient_cost += cached_price * qty_per_craft
            ingredient_depths.append(IngredientDepth(
                item_id=ing_id,
                item_name=ing_name,
                qty_per_craft=qty_per_craft,
                avg_price=cached_price,
                cached_price=cached_price,
                can_fill=True,
                source=source,
            ))
            continue

        # Mall-sourced: check real depth.
        # Use the item name for the search (pudnuggler is a name search).
        ing_name = ing_name or get_item(graph, ing_id).name
        time.sleep(request_delay)
        try:
            ing_search = search_mall(session, ing_name, max_pages=2)
        except Exception as exc:
            log.warning("Ingredient #%d (%s) search failed: %s", ing_id, ing_name, exc)
            # Fall back to cached price but flag depth as unknown
            real_ingredient_cost += cached_price * qty_per_craft
            input_depth_ok = False
            ingredient_depths.append(IngredientDepth(
                item_id=ing_id,
                item_name=ing_name,
                qty_per_craft=qty_per_craft,
                avg_price=cached_price,
                cached_price=cached_price,
                can_fill=False,
                source=source,
            ))
            continue

        ing_name = ing_name or ing_search.item_name
        ing_listings = [l for l in ing_search.listings
                        if l.item_id == 0 or l.item_id == ing_id]
        if not ing_listings:
            ing_listings = ing_search.listings  # fall back if item_id parsing failed
        avg_price, can_fill = buy_depth(ing_listings, qty_per_craft * units)
        if not can_fill:
            input_depth_ok = False

        # If depth check failed to fill, avg_price is for what was fillable.
        # Fall back to cached price when order book is completely empty.
        effective_price = avg_price if avg_price > 0 else cached_price
        real_ingredient_cost += effective_price * qty_per_craft

        ingredient_depths.append(IngredientDepth(
            item_id=ing_id,
            item_name=ing_name,
            qty_per_craft=qty_per_craft,
            avg_price=effective_price,
            cached_price=cached_price,
            can_fill=can_fill,
            source=source,
        ))

    # ── 4. Real profit ────────────────────────────────────────────────────────
    real_craft_cost = real_ingredient_cost + overhead
    real_profit = real_sell_price - real_craft_cost
    real_margin = (real_profit / real_sell_price * 100) if real_sell_price > 0 else 0.0
    depth_ok = sell_depth_ok and input_depth_ok and real_profit > 0

    return VerifiedScanResult(
        **base,
        real_sell_price=real_sell_price,
        real_craft_cost=real_craft_cost,
        real_profit=real_profit,
        real_margin_pct=real_margin,
        depth_ok=depth_ok,
        sell_depth_ok=sell_depth_ok,
        input_depth_ok=input_depth_ok,
        ingredients=ingredient_depths,
    )


# ── Snapshot persistence ──────────────────────────────────────────────────────

def save_verification_snapshots(
    verified: list[VerifiedScanResult],
    db_path: str | Path,
) -> None:
    """
    Persist live-price snapshots from a verification run to DuckDB.

    Snapshots are bucketed to the minute so that re-running within the same
    minute overwrites rather than accumulates duplicate rows.

    Args:
        verified: Output of verify_top_results().
        db_path:  Path to kol.duckdb.
    """
    from kol_data.db.store import KolStore

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    rows = []
    for v in verified:
        if v.error or v.real_sell_price is None:
            continue
        cheapest_ask = int(v.real_sell_price) + 1  # undo the -1 applied during verify
        rows.append((
            v.item_id,
            now,
            cheapest_ask,
            None,                # listings_count — not captured in VerifiedScanResult
            v.real_craft_cost,
            v.real_profit,
        ))

    if not rows:
        return

    with KolStore.open(db_path) as store:
        store.upsert_mall_snapshots(rows)

    log.info("Saved %d mall snapshots to %s", len(rows), db_path)
