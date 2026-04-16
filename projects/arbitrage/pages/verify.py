"""Live order-book verification page — confirms scan candidates with real mall depth."""

from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from calculation.verify import VerifiedScanResult, verify_top_results, save_verification_snapshots
from calculation.loader import DB_PATH
from cli.scan import scan_profitable, ScanResult

st.title("Live Verification")
st.caption("Confirm scan candidates with live mall order-book depth and real margin.")

kol = st.session_state.kol
config = st.session_state.config
G = kol.graph
prices = kol.prices

# Items pre-selected from the Scan page (via "Verify N selected" button)
queued: list[ScanResult] | None = st.session_state.pop("verify_queue", None)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    username = os.environ.get("KOL_USERNAME", "")
    password = os.environ.get("KOL_PASSWORD", "")
    if username and password:
        st.success(f"KoL session ready  ·  **{username}**")
    else:
        missing = [v for v, val in [("KOL_USERNAME", username), ("KOL_PASSWORD", password)] if not val]
        st.error(f"Missing env: {', '.join(missing)}")
        st.caption("Set these before launching the app.")

    st.divider()

    if queued:
        st.info(f"{len(queued)} items from Scan page queued for verification.")
        if st.button("← Back to Scan", use_container_width=True):
            st.switch_page("pages/scan.py")
        st.divider()
        st.header("Verification")
        # No scan filters needed — we already have the items
        verify_units = st.number_input("Batch size (units)", min_value=1, value=10, step=1,
                                       help="Depth check: can the order book fill this many crafts?")
        req_delay    = st.number_input("Request delay (sec)", min_value=0.5, value=3.0, step=0.5)
        top_n        = None  # unused when queued
    else:
        st.header("Scan Filters")
        min_profit = st.number_input("Min Profit (meat)", min_value=0, value=50_000, step=10_000)
        min_margin = st.number_input("Min Margin %", min_value=0.0, value=5.0, step=1.0)

        st.divider()

        st.header("Verification")
        top_n        = st.number_input("Top N to verify", min_value=1, max_value=50, value=10, step=1)
        verify_units = st.number_input("Batch size (units)", min_value=1, value=10, step=1,
                                       help="Depth check: can the order book fill this many crafts?")
        req_delay    = st.number_input("Request delay (sec)", min_value=0.5, value=3.0, step=0.5)

    st.divider()

    can_run = bool(username and password)
    run_btn = st.button(
        "Run Verification",
        type="primary",
        use_container_width=True,
        disabled=not can_run,
        help="Requires KOL_USERNAME and KOL_PASSWORD env vars." if not can_run else "",
    )

# ── Execute ────────────────────────────────────────────────────────────────────
if run_btn:
    if queued:
        candidates = queued
    else:
        with st.spinner("Scanning profitable crafts…"):
            candidates = scan_profitable(
                G, prices, config,
                min_profit=float(min_profit),
                min_margin=float(min_margin),
            )
        if not candidates:
            st.warning("Scan found no profitable crafts. Lower Min Profit or Min Margin.")
            st.stop()
        candidates = candidates[:int(top_n)]

    n = len(candidates)
    est_secs = int(n * 2 * float(req_delay))
    with st.spinner(f"Verifying {n} items with live mall data… (~{est_secs}s)"):
        from kol_session.session import KoLSession
        with KoLSession.from_env() as session:
            verified = verify_top_results(
                session=session,
                results=candidates,
                graph=G,
                prices=prices,
                config=config,
                npc_prices=kol.npc_prices,
                top_n=n,
                units=int(verify_units),
                request_delay=float(req_delay),
            )

    st.session_state.verified_results = verified
    st.session_state.verified_units   = int(verify_units)

    # Persist snapshots so charts accumulate data across runs
    if DB_PATH.exists():
        save_verification_snapshots(verified, DB_PATH)

# ── Guard ──────────────────────────────────────────────────────────────────────
verified: list[VerifiedScanResult] = st.session_state.get("verified_results", [])

if not verified:
    if queued:
        st.info(f"{len(queued)} items queued. Click **Run Verification** to check live depth.")
    else:
        st.info("Configure the sidebar and click **Run Verification**.")
    st.stop()

units_used: int = st.session_state.get("verified_units", 10)


# ── Helpers ────────────────────────────────────────────────────────────────────
def _verdict(v: VerifiedScanResult) -> str:
    if v.error:              return "ERROR"
    if v.depth_ok:           return "OK"
    if not v.input_depth_ok: return "THIN"
    return "NEG"

_VERDICT_ICON  = {"OK": "✓", "THIN": "~", "NEG": "✗", "ERROR": "!"}
_VERDICT_COLOR = {"OK": "#2ecc71", "THIN": "#f39c12", "NEG": "#e74c3c", "ERROR": "#95a5a6"}
_VERDICT_BG    = {"OK": "#0b2016", "THIN": "#261a06", "NEG": "#260a0a", "ERROR": "#1a1a1a"}


# ── Summary metrics ────────────────────────────────────────────────────────────
ok_cnt   = sum(1 for v in verified if _verdict(v) == "OK")
thin_cnt = sum(1 for v in verified if _verdict(v) == "THIN")
neg_cnt  = sum(1 for v in verified if _verdict(v) == "NEG")
err_cnt  = sum(1 for v in verified if _verdict(v) == "ERROR")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Confirmed",  ok_cnt,   help="Positive real profit + order book deep enough")
c2.metric("Thin Depth", thin_cnt, help="Ingredients not fillable at requested batch size")
c3.metric("Neg Margin", neg_cnt,  help="Inputs available but real craft cost > live ask")
c4.metric("Errors",     err_cnt,  help="Network or parse failure during verification")

st.divider()


# ── Summary table ──────────────────────────────────────────────────────────────
rows = []
for v in verified:
    verdict = _verdict(v)
    ask_delta = v.price_delta_pct
    if v.real_profit is not None and v.cached_profit != 0:
        pchg = (v.real_profit - v.cached_profit) / abs(v.cached_profit) * 100
        pchg_str = f"{pchg:+.0f}%"
    else:
        pchg_str = "—"

    rows.append({
        "Verdict":     f"{_VERDICT_ICON[verdict]} {verdict}",
        "Item":        v.item_name,
        "Pricegun":    v.cached_sell_price,
        "Live Ask":    v.real_sell_price,
        "ΔAsk":        f"{ask_delta:+.1f}%" if ask_delta is not None else "—",
        "Est Profit":  v.cached_profit,
        "Live Craft":  v.real_craft_cost,
        "Real Profit": v.real_profit,
        "ΔProfit":     pchg_str,
    })

df_summary = pd.DataFrame(rows)


def _color_verdict(val: str) -> str:
    key = val.split()[-1] if val else "ERROR"
    color = _VERDICT_COLOR.get(key, "#999")
    bg    = _VERDICT_BG.get(key, "#111")
    return f"color: {color}; background-color: {bg}; font-weight: 600; text-align: center;"


def _color_profit(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return "color: #2ecc71;" if val >= 0 else "color: #e74c3c;"


styled = (
    df_summary.style
    .map(_color_verdict, subset=["Verdict"])
    .map(_color_profit, subset=["Real Profit"])
    .format({
        "Pricegun":    "{:,.0f}",
        "Live Ask":    lambda x: f"{x:,.0f}" if x is not None and not (isinstance(x, float) and pd.isna(x)) else "—",
        "Est Profit":  "{:,.0f}",
        "Live Craft":  lambda x: f"{x:,.0f}" if x is not None and not (isinstance(x, float) and pd.isna(x)) else "—",
        "Real Profit": lambda x: f"{x:,.0f}" if x is not None and not (isinstance(x, float) and pd.isna(x)) else "—",
    })
)

st.dataframe(styled, use_container_width=True, hide_index=True)

st.divider()


# ── Per-item expanders ─────────────────────────────────────────────────────────
st.subheader(f"Item Detail  ·  {units_used}-unit batch")

for v in verified:
    verdict = _verdict(v)
    label   = f"{_VERDICT_ICON[verdict]} {v.item_name}"

    with st.expander(label, expanded=(verdict == "OK")):
        if v.error:
            st.error(v.error)
            continue

        # ── 4 key metrics ──────────────────────────────────────────────────────
        mc1, mc2, mc3, mc4 = st.columns(4)

        mc1.metric("Pricegun", f"{v.cached_sell_price:,.0f}",
                   help="Pricegun rolling-average sell price used by the scan")

        if v.real_sell_price is not None:
            ask_delta = v.price_delta_pct
            mc2.metric("Live Ask", f"{v.real_sell_price:,.0f}",
                       delta=f"{ask_delta:+.1f}%" if ask_delta is not None else None,
                       help="Cheapest current mall listing − 1 meat (your undercut price)")
        else:
            mc2.metric("Live Ask", "—")

        mc3.metric("Est Profit", f"{v.cached_profit:,.0f}",
                   help="Pricegun sell − Pricegun craft cost")

        if v.real_profit is not None:
            if v.cached_profit != 0:
                pchg = (v.real_profit - v.cached_profit) / abs(v.cached_profit) * 100
                delta_str = f"{pchg:+.0f}%"
            else:
                delta_str = None
            mc4.metric("Real Profit", f"{v.real_profit:,.0f}",
                       delta=delta_str,
                       delta_color="normal" if v.real_profit >= 0 else "inverse",
                       help="Live Ask − Live Craft cost")
        else:
            mc4.metric("Real Profit", "—")

        # ── Price history + reference lines ───────────────────────────────────
        # Load Pricegun history + recorded live snapshots from DB
        price_data     = prices.get(v.item_id)
        history_daily  = price_data.history_daily  if price_data else []
        history_weekly = price_data.history_weekly if price_data else []
        history        = history_daily or history_weekly

        snapshots: list[dict] = []
        if DB_PATH.exists():
            from kol_data.db.store import KolStore
            with KolStore.open(DB_PATH) as store:
                snapshots = store.load_mall_snapshots(v.item_id)

        if history or snapshots:
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                row_heights=[0.72, 0.28],
                vertical_spacing=0.04,
                subplot_titles=("Price History", "Weekly Volume"),
            )

            if history:
                dates       = [h.date  for h in history]
                hist_prices = [h.price for h in history]
                fig.add_trace(go.Scatter(
                    x=dates, y=hist_prices,
                    name="Pricegun Avg",
                    line=dict(color="#4C9BE8", width=2),
                    hovertemplate="%{x|%b %d, %Y}<br><b>%{y:,.0f} meat</b><extra></extra>",
                ), row=1, col=1)

                vol_history = history_weekly or history_daily
                vol_dates   = [h.date   for h in vol_history]
                volumes     = [h.volume for h in vol_history]
                fig.add_trace(go.Bar(
                    x=vol_dates, y=volumes,
                    name="Volume",
                    marker_color="#4C9BE8",
                    opacity=0.45,
                    hovertemplate="%{x|%b %d}<br><b>%{y} sold</b><extra></extra>",
                ), row=2, col=1)

            # Live snapshots — recorded ask prices over time
            if snapshots:
                snap_times  = [s["captured_at"]  for s in snapshots]
                snap_asks   = [s["cheapest_ask"]  for s in snapshots]
                snap_crafts = [s["real_craft_cost"] for s in snapshots]
                snap_profits = [s["real_profit"]  for s in snapshots]

                fig.add_trace(go.Scatter(
                    x=snap_times, y=snap_asks,
                    name="Live Ask (recorded)",
                    mode="lines+markers",
                    line=dict(color="#2ecc71", width=2),
                    marker=dict(size=7),
                    hovertemplate="%{x|%b %d %H:%M}<br><b>Ask: %{y:,.0f}</b><extra></extra>",
                ), row=1, col=1)

                craft_vals = [c for c in snap_crafts if c is not None]
                if craft_vals:
                    craft_times = [snap_times[i] for i, c in enumerate(snap_crafts) if c is not None]
                    fig.add_trace(go.Scatter(
                        x=craft_times, y=craft_vals,
                        name="Live Craft (recorded)",
                        mode="lines+markers",
                        line=dict(color="#e74c3c", width=2, dash="dot"),
                        marker=dict(size=6),
                        hovertemplate="%{x|%b %d %H:%M}<br><b>Craft: %{y:,.0f}</b><extra></extra>",
                    ), row=1, col=1)

            # Current-run horizontal references (dotted)
            fig.add_hline(
                y=v.cached_sell_price, row=1, col=1,
                line_dash="dot", line_color="#4C9BE8", line_width=1,
                annotation_text=f"Pricegun {v.cached_sell_price:,.0f}",
                annotation_position="top right",
                annotation_font_color="#4C9BE8",
            )
            if v.real_sell_price is not None:
                fig.add_hline(
                    y=v.real_sell_price, row=1, col=1,
                    line_dash="dot", line_color="#2ecc71", line_width=1,
                    annotation_text=f"Live Ask now {v.real_sell_price:,.0f}",
                    annotation_position="top left",
                    annotation_font_color="#2ecc71",
                )
            fig.add_hline(
                y=v.cached_craft_cost, row=1, col=1,
                line_dash="dash", line_color="#f39c12", line_width=1,
                annotation_text=f"Est Craft {v.cached_craft_cost:,.0f}",
                annotation_position="bottom right",
                annotation_font_color="#f39c12",
            )
            if v.real_craft_cost is not None:
                fig.add_hline(
                    y=v.real_craft_cost, row=1, col=1,
                    line_dash="dash", line_color="#e74c3c", line_width=1,
                    annotation_text=f"Live Craft now {v.real_craft_cost:,.0f}",
                    annotation_position="bottom left",
                    annotation_font_color="#e74c3c",
                )

            fig.update_layout(
                height=480,
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(t=40, b=20, l=10, r=10),
            )
            fig.update_yaxes(tickformat=",.0f", title_text="Meat",  row=1, col=1)
            fig.update_yaxes(title_text="Units", row=2, col=1)

            if snapshots:
                st.caption(f"Price history + {len(snapshots)} recorded live snapshot(s)")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No price history available for this item.")

        # ── Ingredient cost breakdown ──────────────────────────────────────────
        mall_ings = [ing for ing in v.ingredients if ing.source == "mall"]
        if mall_ings:
            st.caption("Ingredient Cost Breakdown (mall-sourced, total cost per craft)")

            ing_names    = [ing.item_name or f"#{ing.item_id}" for ing in mall_ings]
            cached_costs = [ing.cached_price * ing.qty_per_craft for ing in mall_ings]
            live_costs   = [ing.avg_price   * ing.qty_per_craft for ing in mall_ings]

            order = sorted(range(len(mall_ings)), key=lambda i: live_costs[i], reverse=True)
            ing_names    = [ing_names[i]    for i in order]
            cached_costs = [cached_costs[i] for i in order]
            live_costs   = [live_costs[i]   for i in order]
            thin_flags   = [not mall_ings[i].can_fill for i in order]

            ing_labels = [
                f"{name}  ⚠ THIN" if thin else name
                for name, thin in zip(ing_names, thin_flags)
            ]

            fig2 = go.Figure()
            fig2.add_trace(go.Bar(
                name="Pricegun cost",
                y=ing_labels,
                x=cached_costs,
                orientation="h",
                marker_color="#4C9BE8",
                opacity=0.75,
                hovertemplate="<b>%{y}</b><br>Pricegun: %{x:,.0f}<extra></extra>",
            ))
            fig2.add_trace(go.Bar(
                name="Live cost",
                y=ing_labels,
                x=live_costs,
                orientation="h",
                marker_color="#e74c3c",
                opacity=0.75,
                hovertemplate="<b>%{y}</b><br>Live: %{x:,.0f}<extra></extra>",
            ))
            fig2.update_layout(
                barmode="group",
                height=max(180, len(mall_ings) * 55 + 80),
                margin=dict(t=20, b=20, l=10, r=40),
                xaxis=dict(tickformat=",.0f", title="Total cost (qty × unit price, meat)"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig2, use_container_width=True)

            for ing in mall_ings:
                if not ing.can_fill:
                    name   = ing.item_name or f"#{ing.item_id}"
                    needed = ing.qty_per_craft * units_used
                    st.warning(f"**{name}**: need {needed:,} units for {units_used} crafts — order book too shallow to fill")

        npc_ings = [ing for ing in v.ingredients if ing.source != "mall"]
        if npc_ings:
            with st.expander("NPC / fixed-price ingredients", expanded=False):
                npc_df = pd.DataFrame([
                    {
                        "Ingredient": ing.item_name or f"#{ing.item_id}",
                        "Source":     ing.source,
                        "Qty/craft":  ing.qty_per_craft,
                        "Unit price": ing.cached_price,
                        "Total":      ing.cached_price * ing.qty_per_craft,
                    }
                    for ing in npc_ings
                ])
                st.dataframe(
                    npc_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Unit price": st.column_config.NumberColumn(format="%,.0f"),
                        "Total":      st.column_config.NumberColumn(format="%,.0f"),
                    },
                )
