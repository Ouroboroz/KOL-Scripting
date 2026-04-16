from __future__ import annotations

from dataclasses import dataclass, field
import networkx as nx

from kol_data.models.item import Concoction, Item
from kol_data.models.price import PriceData
from kol_data.graph.queries import get_item
from kol_data.graph.node_types import item_key

from calculation.config import CraftingConfig


@dataclass
class CostStep:
    item_id: int
    item_name: str
    quantity: int
    unit_cost: float
    source: str          # "mall", "autosell_fallback", "crafted", "overhead"
    method: str | None
    method_overhead: float


@dataclass
class CraftingCostResult:
    item_id: int
    item_name: str
    total_cost: float | None      # None if no valid path
    buy_cost: float | None        # Effective buy cost = min(mall, npc) — used in profit calc
    mall_price: float | None = None  # Raw mall listing price (before NPC comparison)
    breakdown: list[CostStep] = field(default_factory=list)
    missing_prices: list[int] = field(default_factory=list)
    unavailable: bool = False
    chosen_concoction_id: int | None = None
    recipe_comment: str | None = None


def _buy_cost(
    G: nx.DiGraph,
    prices: dict[int, PriceData],
    item_id: int,
    npc_prices: dict[int, int] | None = None,
) -> float | None:
    item = get_item(G, item_id)
    price_data = prices.get(item_id)
    mall = price_data.latest_price() if price_data and price_data.latest_price() is not None else None
    npc = npc_prices.get(item_id) if npc_prices else None
    candidates = [p for p in [mall, npc] if p is not None]
    if candidates:
        return min(candidates)
    if item.autosell > 0:
        return float(item.autosell)
    return None


def _price_source(
    price_data: PriceData | None,
    item_id: int,
    npc_prices: dict[int, int] | None = None,
) -> str:
    mall = price_data.latest_price() if price_data and price_data.latest_price() is not None else None
    npc = npc_prices.get(item_id) if npc_prices else None
    if mall is not None and (npc is None or mall <= npc):
        return "mall"
    if npc is not None:
        return "npc"
    return "autosell_fallback"


def _cost_concoction(
    G: nx.DiGraph,
    prices: dict[int, PriceData],
    conc: Concoction,
    config: CraftingConfig,
    npc_prices: dict[int, int] | None,
    used_free: dict[str, int],
    visited: set[int],
) -> tuple[float, list[CostStep], list[int]] | None:
    # ALL methods must be available — they are requirements, not alternatives.
    # e.g. ['SMITH', 'GRIMACITE'] means "needs smithing AND grimacite access".
    if not conc.methods or not all(config.is_method_available(m) for m in conc.methods):
        return None

    # Pick the method with the lowest adventure cost for overhead calculation.
    best_overhead: float | None = None
    best_method: str | None = None
    for m in conc.methods:
        cost = config.adventure_cost(m, dict(used_free))
        if cost is not None and (best_overhead is None or cost < best_overhead):
            best_overhead = cost
            best_method = m

    if best_method is None:
        return None

    config.adventure_cost(best_method, used_free)

    steps: list[CostStep] = []
    missing: list[int] = []
    total = best_overhead or 0.0

    for ing in conc.ingredients:
        ing_id = ing.item_id
        qty = ing.quantity

        if not G.has_node(item_key(ing_id)):
            missing.append(ing_id)
            continue

        if ing_id not in visited:
            sub = compute_crafting_cost(G, prices, ing_id, config, npc_prices, used_free, visited | {ing_id})
            if sub.total_cost is not None and (
                sub.buy_cost is None or sub.total_cost < sub.buy_cost
            ):
                total += sub.total_cost * qty
                steps.append(CostStep(
                    item_id=ing_id,
                    item_name=get_item(G, ing_id).name,
                    quantity=qty,
                    unit_cost=sub.total_cost,
                    source="crafted",
                    method=best_method,
                    method_overhead=0.0,
                ))
                steps.extend(sub.breakdown)
                missing.extend(sub.missing_prices)
                continue

        price = _buy_cost(G, prices, ing_id, npc_prices)
        if price is None:
            missing.append(ing_id)
            continue

        steps.append(CostStep(
            item_id=ing_id,
            item_name=get_item(G, ing_id).name,
            quantity=qty,
            unit_cost=price,
            source=_price_source(prices.get(ing_id), ing_id, npc_prices),
            method=None,
            method_overhead=0.0,
        ))
        total += price * qty

    if best_overhead:
        steps.append(CostStep(
            item_id=-1,
            item_name="overhead",
            quantity=1,
            unit_cost=best_overhead,
            source="overhead",
            method=best_method,
            method_overhead=best_overhead,
        ))

    return total, steps, missing


def compute_crafting_cost(
    G: nx.DiGraph,
    prices: dict[int, PriceData],
    item_id: int,
    config: CraftingConfig,
    npc_prices: dict[int, int] | None = None,
    used_free: dict[str, int] | None = None,
    visited: set[int] | None = None,
) -> CraftingCostResult:
    if used_free is None:
        used_free = config.fresh_used_free()
    if visited is None:
        visited = {item_id}

    item = get_item(G, item_id)
    price_data = prices.get(item_id)
    mall_price = price_data.latest_price() if price_data and price_data.latest_price() is not None else None
    buy_cost = _buy_cost(G, prices, item_id, npc_prices)

    if not item.concoctions:
        if buy_cost is None:
            return CraftingCostResult(
                item_id=item_id, item_name=item.name,
                total_cost=None, buy_cost=None, mall_price=mall_price,
                missing_prices=[item_id], unavailable=True,
            )
        # No recipe — buy only. total_cost=None signals "not craftable" to
        # callers; _cost_concoction falls through to _buy_cost for ingredients.
        return CraftingCostResult(
            item_id=item_id, item_name=item.name,
            total_cost=None, buy_cost=buy_cost, mall_price=mall_price,
        )

    best_cost: float | None = None
    best_steps: list[CostStep] = []
    best_missing: list[int] = []
    best_conc_id: int | None = None
    best_comment: str | None = None
    any_available = False

    for conc in item.concoctions:
        # Skip self-referential concoctions (all ingredients are the item itself).
        # This is how data.loathers.net represents NPC-shop-purchasable items —
        # they're not actually craftable.
        if conc.ingredients and all(ing.item_id == item_id for ing in conc.ingredients):
            continue
        used_free_copy = dict(used_free)
        result = _cost_concoction(G, prices, conc, config, npc_prices, used_free_copy, visited)
        if result is None:
            continue

        any_available = True
        cost, steps, missing = result

        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_steps = steps
            best_missing = missing
            best_conc_id = conc.id
            best_comment = conc.comment
            used_free.update(used_free_copy)

    if not any_available:
        return CraftingCostResult(
            item_id=item_id, item_name=item.name,
            total_cost=None, buy_cost=buy_cost, mall_price=mall_price,
            unavailable=True,
        )

    return CraftingCostResult(
        item_id=item_id, item_name=item.name,
        total_cost=best_cost, buy_cost=buy_cost, mall_price=mall_price,
        breakdown=best_steps,
        missing_prices=best_missing,
        chosen_concoction_id=best_conc_id,
        recipe_comment=best_comment,
    )
