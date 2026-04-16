"""
uv run python -m cli.query "dry noodles"
uv run python -m cli.query 435
uv run python -m cli.query "depleted Grimacite"          # substring search
uv run python -m cli.query "Mae West" --adventure-cost 5000 --free-cooks 3
uv run python -m cli.query "long pork lasagna" --methods
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from kol_data.data import PriceMode
from kol_data.graph.queries import get_item

from calculation.config import CraftingConfig
from calculation.cost import compute_crafting_cost, CraftingCostResult
from calculation.loader import load_kol_data, find_items_db, find_items_graph

HERE = Path(__file__).parent.parent
CONFIG_PATH = HERE / "config.toml"


def _fmt(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:,.0f} meat"


def print_config(config) -> None:
    free_parts = []
    if config.free_cooks:  free_parts.append(f"cooks: {config.free_cooks}")
    if config.free_mixes:  free_parts.append(f"mixes: {config.free_mixes}")
    if config.free_smiths: free_parts.append(f"smiths: {config.free_smiths}")
    if config.free_stills: free_parts.append(f"stills: {config.free_stills}")
    free_str = "  free " + "  ".join(free_parts) if free_parts else "  no free crafts"
    combine_str = "free (plunger)" if config.plunger_active else f"{config.combine_cost:.0f} meat"
    extras = []
    if config.moon_sign:
        extras.append(f"sign: {config.moon_sign}")
    if config.character_class:
        extras.append(f"class: {config.character_class}")
    if config.accessible_store_ids:
        extras.append(f"shops: {', '.join(sorted(config.accessible_store_ids))}")
    extra_str = "  " + "  ".join(extras) if extras else ""
    print(f"Config: {config.meat_per_adventure:,.0f} meat/adv  |  combine: {combine_str}{free_str}{extra_str}")


def print_result(result: CraftingCostResult, npc_prices: dict[int, int] | None = None) -> None:
    bar = "─" * 44
    print(f"\nItem: {result.item_name} (#{result.item_id})")
    print(bar)

    npc = npc_prices.get(result.item_id) if npc_prices else None

    if result.unavailable:
        print("  Cannot be crafted with current config")
        print(f"  Mall price:  {_fmt(result.mall_price)}")
        if npc is not None:
            print(f"  NPC price:   {_fmt(float(npc))}")
        return

    craft = result.total_cost
    buy = result.buy_cost

    print(f"  Mall price:  {_fmt(result.mall_price)}")
    if npc is not None:
        print(f"  NPC price:   {_fmt(float(npc))}")

    if craft is None:
        print("  Not craftable")
        return

    cheaper = "  ← cheaper to craft" if craft < buy else "  ← cheaper to buy"
    print(f"  Craft cost:  {_fmt(craft)}{cheaper}")
    print(bar)

    ing_steps = [s for s in result.breakdown if s.source != "overhead"]
    if ing_steps:
        print("Ingredients:")
        for step in ing_steps:
            tag = f"({step.source})"
            print(f"  {step.item_name} x{step.quantity:<3}  {_fmt(step.unit_cost * step.quantity):>14}  {tag}")

    overhead_steps = [s for s in result.breakdown if s.source == "overhead"]
    if overhead_steps:
        print("Crafting overhead:")
        for step in overhead_steps:
            overhead_str = _fmt(step.method_overhead) if step.method_overhead else "0 meat (free craft used)"
            print(f"  {step.method} step             {overhead_str:>14}")

    if result.missing_prices:
        print(f"Missing prices: item IDs {result.missing_prices}")
    else:
        print("Missing prices: none")

    if result.recipe_comment:
        print(f"Recipe note:    {result.recipe_comment}")
    else:
        print("Recipe note:    none")


def _resolve_item(query: str, kol_data_graph, need_graph_fallback: bool) -> int | None:
    """
    Resolve a name/ID query to a single item_id.
    Uses DuckDB first (fast), falls back to graph scan.
    Prints a disambiguation list and returns None if multiple matches.
    """
    G = kol_data_graph

    # Numeric ID: direct lookup
    stripped = query.strip()
    if stripped.lstrip("-").isdigit():
        iid = int(stripped)
        from kol_data.graph.node_types import item_key
        return iid if G.has_node(item_key(iid)) else None

    # DuckDB text search
    matches = find_items_db(query)
    if not matches and need_graph_fallback:
        matches = find_items_graph(G, query)

    if len(matches) == 1:
        return matches[0][0]

    if len(matches) == 0:
        print(f"Item not found: {query!r}", file=sys.stderr)
        return None

    # Multiple — show list
    print(f"Multiple items match {query!r}:")
    for iid, name in sorted(matches, key=lambda x: x[1]):
        print(f"  #{iid:<6}  {name}")
    print("Be more specific or use the item ID.", file=sys.stderr)
    return None


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(prog="cli.query", description="KoL crafting cost query")
    parser.add_argument("item", help="Item name (quoted) or numeric item ID")
    parser.add_argument("--adventure-cost", type=float)
    parser.add_argument("--free-cooks", type=int)
    parser.add_argument("--free-mixes", type=int)
    parser.add_argument("--free-smiths", type=int)
    parser.add_argument("--free-stills", type=int)
    parser.add_argument("--methods", action="store_true", help="Show raw concoction methods and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    config = CraftingConfig.from_toml(CONFIG_PATH) if CONFIG_PATH.exists() else CraftingConfig()
    if args.adventure_cost is not None:
        config.meat_per_adventure = args.adventure_cost
    if args.free_cooks is not None:
        config.free_cooks = args.free_cooks
    if args.free_mixes is not None:
        config.free_mixes = args.free_mixes
    if args.free_smiths is not None:
        config.free_smiths = args.free_smiths
    if args.free_stills is not None:
        config.free_stills = args.free_stills

    # --methods only needs the graph structure, never prices
    price_mode = PriceMode.NONE if args.methods else PriceMode.CACHED
    kol = load_kol_data(config, price_mode=price_mode)

    item_id = _resolve_item(args.item, kol.graph, need_graph_fallback=True)
    if item_id is None:
        sys.exit(1)

    if args.methods:
        item = get_item(kol.graph, item_id)
        print(f"{item.name} (#{item_id}) — {len(item.concoctions)} concoction(s)")
        for i, conc in enumerate(item.concoctions):
            ings = ", ".join(f"#{ing.item_id} x{ing.quantity}" for ing in conc.ingredients)
            print(f"  [{i}] concoction #{conc.id}  methods={conc.methods}  ingredients=[{ings}]")
            if conc.comment:
                print(f"       comment: {conc.comment}")
        return

    print_config(config)
    result = compute_crafting_cost(kol.graph, kol.prices, item_id, config, kol.npc_prices)
    print_result(result, npc_prices=kol.npc_prices)


if __name__ == "__main__":
    main()
