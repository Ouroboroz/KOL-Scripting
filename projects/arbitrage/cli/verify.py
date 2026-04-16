"""Display helpers for VerifiedScanResult CLI output."""

from __future__ import annotations

from calculation.verify import VerifiedScanResult


def print_verified_results(verified: list[VerifiedScanResult]) -> None:
    """Print a table of depth-verified scan results."""
    if not verified:
        print("No verified results.")
        return

    ok_count = sum(1 for v in verified if v.depth_ok)
    print(f"\n{'─' * 122}")
    print(f"Depth Verification  ({ok_count}/{len(verified)} confirmed at {verified[0].units_verified}-unit batch size)")
    print(f"{'─' * 122}")

    header = (
        f"{'Item':<35} {'Pricegun':>12} {'Live Ask':>12} {'ΔAsk':>7} "
        f"{'Est Profit':>12} {'Live Craft':>12} {'Real Profit':>12} {'ΔProfit':>8} {'Depth':>7}"
    )
    print(header)
    print("─" * len(header))

    for v in verified:
        if v.error:
            print(f"{v.item_name:<35}  ERROR: {v.error}")
            continue

        ask_delta = v.price_delta_pct
        ask_str   = f"{ask_delta:+.1f}%" if ask_delta is not None else "  n/a"

        if v.real_profit is not None and v.cached_profit != 0:
            profit_chg = (v.real_profit - v.cached_profit) / abs(v.cached_profit) * 100
            profit_chg_str = f"{profit_chg:+.0f}%"
        else:
            profit_chg_str = "  n/a"

        if not v.depth_ok:
            if not v.input_depth_ok:
                depth_label = "✗ THIN"
            else:
                depth_label = "✗ NEG"
        else:
            depth_label = "✓ OK"

        live_craft_str  = f"{v.real_craft_cost:,.0f}"  if v.real_craft_cost  is not None else "n/a"
        real_profit_str = f"{v.real_profit:,.0f}"       if v.real_profit      is not None else "n/a"
        live_ask_str    = f"{v.real_sell_price:,.0f}"   if v.real_sell_price  is not None else "n/a"

        print(
            f"{v.item_name:<35} "
            f"{v.cached_sell_price:>12,.0f} {live_ask_str:>12} {ask_str:>7} "
            f"{v.cached_profit:>12,.0f} {live_craft_str:>12} {real_profit_str:>12} "
            f"{profit_chg_str:>8} {depth_label:>7}"
        )

        # Ingredient detail when depth is thin or any ingredient moved >10%
        show_ings = (
            not v.input_depth_ok
            or any(
                ing.source == "mall"
                and ing.cached_price > 0
                and abs(ing.avg_price - ing.cached_price) / ing.cached_price > 0.10
                for ing in v.ingredients
            )
        )
        if show_ings:
            for ing in v.ingredients:
                if ing.source != "mall":
                    continue
                fill_flag = " ← THIN" if not ing.can_fill else ""
                pct = ((ing.avg_price - ing.cached_price) / ing.cached_price * 100
                       if ing.cached_price > 0 else 0.0)
                ing_name = ing.item_name or f"#{ing.item_id}"
                print(
                    f"  {'':33} {ing_name:<28} "
                    f"pricegun {ing.cached_price:>9,.0f}  "
                    f"live {ing.avg_price:>9,.0f}  "
                    f"({pct:+.1f}%)  x{ing.qty_per_craft}{fill_flag}"
                )

    print()
    print(
        "Columns:\n"
        "  Pricegun    = Pricegun rolling-average sell price (what the scan used)\n"
        "  Live Ask    = Cheapest current mall listing - 1 meat (what you'd undercut to)\n"
        "  ΔAsk        = (Live Ask - Pricegun) / Pricegun — how stale the Pricegun price is\n"
        "  Est Profit  = Pricegun sell − Pricegun craft cost (what the scan showed)\n"
        "  Live Craft  = Real craft cost using live order-book ingredient prices\n"
        "  Real Profit = Live Ask − Live Craft (actual margin you'd earn)\n"
        "  ΔProfit     = (Real Profit − Est Profit) / |Est Profit|\n"
        "\n"
        "Verdicts:\n"
        "  ✓ OK   = positive real profit + all ingredients fillable at listed depth\n"
        "  ✗ THIN = order book too shallow to buy enough ingredients for a full batch\n"
        "  ✗ NEG  = ingredients available but real craft cost exceeds live sell price"
    )
