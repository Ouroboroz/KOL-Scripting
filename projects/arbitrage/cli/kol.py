"""
Unified KoL arbitrage CLI.

With uv (from anywhere in the workspace):
    uv run kol build
    uv run kol build --force
    uv run kol prices [--force]
    uv run kol scan [--top 20] [--min-profit 5000] [--min-margin 10] [--min-volume 5]
    uv run kol query "long pork lasagna"
    uv run kol query "depleted Grimacite"    # substring search, lists all matches
    uv run kol query 435                     # by item ID
    uv run kol query "item name" --methods   # show raw concoction data

With venv activated (source .venv/bin/activate):
    kol build / kol scan / kol query ...
"""

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).parent.parent
CONFIG_PATH = HERE / "config.toml"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kol",
        description="KoL crafting arbitrage tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'kol <command> --help' for per-command options.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # ── build ──────────────────────────────────────────────────────────────────
    p_build = sub.add_parser("build", help="Fetch items + prices, populate DB")
    p_build.add_argument("--force", action="store_true", help="Re-fetch even if cache is fresh")

    # ── prices ─────────────────────────────────────────────────────────────────
    p_prices = sub.add_parser("prices", help="Refresh prices only (graph cache must exist)")
    p_prices.add_argument("--force", action="store_true", help="Re-fetch even if prices are fresh")

    # ── scan ───────────────────────────────────────────────────────────────────
    p_scan = sub.add_parser("scan", help="Scan for profitable crafting opportunities")
    p_scan.add_argument("--min-profit", type=float, default=0, metavar="MEAT")
    p_scan.add_argument("--min-margin", type=float, default=0, metavar="PCT")
    p_scan.add_argument("--min-volume", type=int, default=0, metavar="UNITS")
    p_scan.add_argument("--top", type=int, default=50)
    p_scan.add_argument(
        "--verify", type=int, default=0, metavar="N",
        help="Verify top N results against live mall order books (requires KOL_USERNAME/KOL_PASSWORD)",
    )
    p_scan.add_argument(
        "--verify-units", type=int, default=10, metavar="UNITS",
        help="Batch size for depth check: how many crafts to verify supply for (default: 10)",
    )
    p_scan.add_argument(
        "--request-delay", type=float, default=3.0, metavar="SECS",
        help="Seconds between mall requests during verification (default: 3.0)",
    )
    p_scan.add_argument("-v", "--verbose", action="store_true")

    # ── query ──────────────────────────────────────────────────────────────────
    p_query = sub.add_parser("query", help="Look up an item's crafting cost")
    p_query.add_argument("item", help="Item name (quoted) or numeric item ID")
    p_query.add_argument("--adventure-cost", type=float, metavar="MEAT")
    p_query.add_argument("--free-cooks", type=int)
    p_query.add_argument("--free-mixes", type=int)
    p_query.add_argument("--free-smiths", type=int)
    p_query.add_argument("--free-stills", type=int)
    p_query.add_argument("--methods", action="store_true", help="Show raw concoction methods")
    p_query.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    # ── dispatch ───────────────────────────────────────────────────────────────

    if args.command == "build":
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        from kol_data.__main__ import cmd_build
        cmd_build(force=args.force)

    elif args.command == "prices":
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        from kol_data.__main__ import cmd_prices
        cmd_prices(force=args.force)

    elif args.command == "scan":
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.WARNING,
            format="%(levelname)s %(message)s",
        )
        from kol_data.data import PriceMode
        from calculation.config import CraftingConfig
        from calculation.loader import load_kol_data
        from cli.scan import scan_profitable, _print_results
        from cli.verify import print_verified_results

        config = CraftingConfig.from_toml(CONFIG_PATH) if CONFIG_PATH.exists() else CraftingConfig()
        kol = load_kol_data(config, price_mode=PriceMode.AUTO)

        print("Scanning all craftable items...")
        results = scan_profitable(
            kol.graph, kol.prices, config,
            npc_prices=kol.npc_prices,
            min_profit=args.min_profit,
            min_margin=args.min_margin,
            min_volume=args.min_volume,
        )
        _print_results(results, config, top=args.top)

        if args.verify > 0:
            from kol_session.session import KoLSession
            from calculation.verify import verify_top_results

            n = min(args.verify, len(results))
            print(f"\nVerifying top {n} results against live order books "
                  f"({args.verify_units}-unit batches, {args.request_delay}s delay)...")
            with KoLSession.from_env() as session:
                verified = verify_top_results(
                    session=session,
                    results=results,
                    graph=kol.graph,
                    prices=kol.prices,
                    config=config,
                    npc_prices=kol.npc_prices,
                    top_n=n,
                    units=args.verify_units,
                    request_delay=args.request_delay,
                )
            print_verified_results(verified)

    elif args.command == "query":
        logging.basicConfig(
            level=logging.INFO if args.verbose else logging.WARNING,
            format="%(levelname)s %(message)s",
        )
        from kol_data.data import PriceMode
        from kol_data.graph.queries import get_item
        from calculation.config import CraftingConfig
        from calculation.cost import compute_crafting_cost
        from calculation.loader import load_kol_data, find_items_db, find_items_graph
        from cli.query import print_result, _resolve_item

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

        from cli.query import print_config
        print_config(config)
        result = compute_crafting_cost(kol.graph, kol.prices, item_id, config, kol.npc_prices)
        print_result(result, npc_prices=kol.npc_prices)


if __name__ == "__main__":
    main()
