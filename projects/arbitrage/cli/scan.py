"""
uv run python -m cli.scan
uv run python -m cli.scan --top 20 --min-profit 5000 --min-margin 10
"""

from __future__ import annotations

import argparse
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

import networkx as nx

from kol_data.data import PriceMode
from kol_data.models.price import PriceData
from kol_data.graph.queries import get_item, item_ids

from calculation.config import CraftingConfig
from calculation.cost import compute_crafting_cost
from calculation.loader import load_kol_data
from cli.query import print_config

HERE = Path(__file__).parent.parent
CONFIG_PATH = HERE / "config.toml"


@dataclass
class ScanResult:
    item_id: int
    item_name: str
    craft_cost: float
    mall_price: float
    profit: float
    margin_pct: float
    net_score: float        # profit * avg_weekly_volume — expected weekly earnings
    method: str
    volume: int             # last active weekly bucket volume (may be weeks old)
    avg_weekly_volume: float  # average volume per week over last CONSISTENCY_WINDOW_WEEKS
    recipe_comment: str | None
    # Market signal fields
    price_trend_pct: float   # % price change over last 4 weekly buckets (neg = falling)
    volatility_pct: float    # coefficient of variation of history prices (higher = wilder)
    sales_conf: float | None # avg recent sale price / current_price (None if no sales data)
    volume_consistency: float  # fraction of weekly buckets with volume > 0 (1.0 = sold every week)

    @property
    def trend_label(self) -> str:
        t = self.price_trend_pct
        if t <= -5:
            return f"↓{abs(t):.0f}%"
        if t >= 5:
            return f"↑{t:.0f}%"
        return f"→{t:+.0f}%"

    @property
    def conf_label(self) -> str:
        if self.sales_conf is None:
            return "n/a"
        return f"{self.sales_conf:.2f}x"


def _price_trend(price_data: PriceData, n: int = 4) -> float:
    """% price change from oldest to newest over last n weekly buckets."""
    buckets = [b for b in price_data.history_weekly[-n:] if b.price is not None]
    if len(buckets) < 2:
        return 0.0
    oldest, newest = buckets[0].price, buckets[-1].price
    if oldest == 0:
        return 0.0
    return ((newest - oldest) / oldest) * 100


def _volatility(price_data: PriceData) -> float:
    """Coefficient of variation (std/mean * 100) across daily history buckets."""
    prices = [b.price for b in price_data.history_daily if b.price is not None]
    if len(prices) < 2:
        return 0.0
    mean = statistics.mean(prices)
    if mean == 0:
        return 0.0
    return (statistics.stdev(prices) / mean) * 100


CONSISTENCY_WINDOW_WEEKS = 12


def _volume_consistency(price_data: PriceData) -> float:
    """Fraction of the last CONSISTENCY_WINDOW_WEEKS calendar weeks that had any sales.

    The pricegun API only returns weeks with activity (zero-volume weeks are absent),
    so we count how many recorded weekly buckets fall within the window vs. the window
    size — not the fraction of stored buckets with volume > 0 (that would always be 1.0).
    """
    if not price_data.history_weekly:
        return 0.0
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=CONSISTENCY_WINDOW_WEEKS)
    active = sum(1 for b in price_data.history_weekly if b.date >= cutoff)
    return active / CONSISTENCY_WINDOW_WEEKS


def _avg_weekly_volume(price_data: PriceData) -> float:
    """Average units sold per calendar week over the last CONSISTENCY_WINDOW_WEEKS weeks.

    Divides total volume in the window by CONSISTENCY_WINDOW_WEEKS (not by active weeks),
    so a monthly batch buyer doesn't look like a steady seller.
    """
    if not price_data.history_weekly:
        return 0.0
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=CONSISTENCY_WINDOW_WEEKS)
    total = sum(b.volume for b in price_data.history_weekly if b.date >= cutoff)
    return total / CONSISTENCY_WINDOW_WEEKS


def _sales_confirmation(price_data: PriceData) -> float | None:
    """Avg recent sale price / current_price. >1 means sales are above the rolling avg."""
    if not price_data.sales or not price_data.current_price:
        return None
    sale_prices = [s.unit_price for s in price_data.sales if s.unit_price is not None]
    if not sale_prices:
        return None
    return statistics.mean(sale_prices) / price_data.current_price


def scan_profitable(
    G: nx.DiGraph,
    prices: dict[int, PriceData],
    config: CraftingConfig,
    npc_prices: dict[int, int] | None = None,
    min_profit: float = 0,
    min_margin: float = 0,
    min_volume: int = 0,
    methods_filter: set[str] | None = None,
) -> list[ScanResult]:
    results = []

    for node_id in item_ids(G):
        if node_id in config.ignored_items:
            continue

        item = get_item(G, node_id)
        price_data = prices.get(node_id)

        if not item.concoctions:
            continue
        if price_data is None or price_data.latest_price() is None:
            continue

        result = compute_crafting_cost(G, prices, node_id, config, npc_prices)

        if result.unavailable or result.total_cost is None or result.buy_cost is None:
            continue
        if result.missing_prices:
            continue

        profit = result.buy_cost - result.total_cost
        if profit <= 0:
            continue

        margin = (profit / result.buy_cost) * 100

        if profit < min_profit or margin < min_margin:
            continue

        # Primary crafting method from winning concoction
        method = "UNKNOWN"
        if result.chosen_concoction_id is not None:
            for conc in item.concoctions:
                if conc.id == result.chosen_concoction_id:
                    method = conc.methods[0] if conc.methods else "UNKNOWN"
                    break

        if methods_filter and method not in methods_filter:
            continue

        # Volume from most recent weekly history bucket (may be weeks old)
        volume = price_data.history_weekly[-1].volume if price_data.history_weekly else 0
        avg_vol = _avg_weekly_volume(price_data)

        if avg_vol < min_volume:
            continue

        results.append(ScanResult(
            item_id=node_id,
            item_name=item.name,
            craft_cost=result.total_cost,
            mall_price=result.buy_cost,
            profit=profit,
            margin_pct=margin,
            net_score=profit * avg_vol,
            method=method,
            volume=volume,
            avg_weekly_volume=avg_vol,
            recipe_comment=result.recipe_comment,
            price_trend_pct=_price_trend(price_data),
            volatility_pct=_volatility(price_data),
            sales_conf=_sales_confirmation(price_data),
            volume_consistency=_volume_consistency(price_data),
        ))

    # Sort by net_score (profit × volume) — best actual weekly earnings first
    results.sort(key=lambda r: r.net_score, reverse=True)
    return results


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(prog="cli.scan")
    parser.add_argument("--min-profit", type=float, default=0)
    parser.add_argument("--min-margin", type=float, default=0, help="Minimum margin %%")
    parser.add_argument("--min-volume", type=int, default=0, help="Minimum recent weekly volume")
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    config = CraftingConfig.from_toml(CONFIG_PATH) if CONFIG_PATH.exists() else CraftingConfig()
    kol = load_kol_data(config, price_mode=PriceMode.AUTO)

    print("Scanning all craftable items...")
    results = scan_profitable(kol.graph, kol.prices, config,
        npc_prices=kol.npc_prices,
        min_profit=args.min_profit,
        min_margin=args.min_margin,
        min_volume=args.min_volume,
    )
    _print_results(results, config, top=args.top)


def _print_results(results: list[ScanResult], config: CraftingConfig, top: int = 50) -> None:
    display = results[:top]
    if not display:
        print("No profitable crafts found with current filters.")
        return

    header = (
        f"{'Item':<35} {'Method':<10} {'Craft':>12} {'Mall':>12} "
        f"{'Profit':>12} {'Mgn':>6} {'Avg/wk':>7} {'Net Score':>14} "
        f"{'Trend':>7} {'Vol%':>6} {'SalesConf':>9} {'Consist':>7}"
    )
    print(header)
    print("─" * len(header))
    for r in display:
        print(
            f"{r.item_name:<35} {r.method:<10} "
            f"{r.craft_cost:>12,.0f} {r.mall_price:>12,.0f} "
            f"{r.profit:>12,.0f} {r.margin_pct:>5.1f}% {r.avg_weekly_volume:>7.1f} "
            f"{r.net_score:>14,.0f} "
            f"{r.trend_label:>7} {r.volatility_pct:>5.1f}% {r.conf_label:>9} "
            f"{r.volume_consistency:>6.0%}"
        )
    print(f"\n{len(results)} profitable items total (showing top {len(display)})")
    print(f"\nAvg/wk: avg weekly volume over last {CONSISTENCY_WINDOW_WEEKS} weeks  Consist: active weeks / {CONSISTENCY_WINDOW_WEEKS}  Trend: price change over 4 weeks  Vol%: price volatility  SalesConf: recent sales / rolling avg")
    print()
    print_config(config)


if __name__ == "__main__":
    main()
