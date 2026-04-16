import time

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from kol_data.graph.queries import get_item, item_ids
from cli.scan import scan_profitable, ScanResult

st.title("Profitable Crafts")

kol = st.session_state.kol
config = st.session_state.config
G = kol.graph
prices = kol.prices

SCAN_CACHE_TTL = 15 * 60  # 15 minutes

# ── Sidebar filters ────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    min_profit = st.number_input("Min Profit (meat)", min_value=0, value=1_000, step=500)
    min_margin = st.number_input("Min Margin %", min_value=0.0, value=0.0, step=1.0)
    min_volume = st.number_input("Min Avg Weekly Volume", min_value=0, value=0, step=1)
    top_n      = st.number_input("Show top N results", min_value=1, value=30, step=5)

    all_methods = sorted({
        m
        for node_id in item_ids(G)
        for conc in get_item(G, node_id).concoctions
        for m in conc.methods
    })
    selected_methods = st.multiselect("Methods", all_methods, default=all_methods)

    st.divider()

    # Cache status
    last_at = st.session_state.get("scan_results_at")
    if last_at:
        age = int(time.time() - last_at)
        age_str = f"{age // 60}m {age % 60}s ago"
        remaining = max(0, SCAN_CACHE_TTL - age)
        st.caption(f"Last scan: {age_str}  ·  refreshes in {remaining // 60}m {remaining % 60}s")

    run_scan = st.button("Run Scan", type="primary", use_container_width=True)

# ── Auto-run / cache ───────────────────────────────────────────────────────────
scan_params = (min_profit, min_margin, min_volume, tuple(sorted(selected_methods)))
cache_stale = (
    "scan_results" not in st.session_state
    or last_at is None
    or time.time() - last_at > SCAN_CACHE_TTL
    or st.session_state.get("scan_params") != scan_params
)

if run_scan or cache_stale:
    with st.spinner(f"Scanning {len(item_ids(G)):,} items..."):
        results = scan_profitable(
            G, prices, config,
            min_profit=float(min_profit),
            min_margin=float(min_margin),
            min_volume=int(min_volume),
            methods_filter=set(selected_methods) if selected_methods else None,
        )
    st.session_state.scan_results    = results
    st.session_state.scan_results_at = time.time()
    st.session_state.scan_params     = scan_params

results: list[ScanResult] = st.session_state.get("scan_results", [])
page_results = results[:int(top_n)]

# ── Summary metrics ────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Profitable Items", f"{len(results):,}", help=f"Showing top {int(top_n)}")
if results:
    col2.metric("Best Profit",    f"{results[0].profit:,.0f} meat")
    col3.metric("Best Margin",    f"{max(r.margin_pct for r in results):.1f}%")
    col4.metric("Best Net Score", f"{results[0].net_score:,.0f}")

st.divider()

if not results:
    st.info("No profitable crafts found with current filters. Try lowering Min Profit or Min Margin.")
    st.stop()

# ── Results table ──────────────────────────────────────────────────────────────
df = pd.DataFrame([
    {
        "Item":        r.item_name,
        "Method":      r.method,
        "Craft Cost":  r.craft_cost,
        "Mall Price":  r.mall_price,
        "Profit":      r.profit,
        "Margin %":    round(r.margin_pct, 1),
        "Avg Vol/wk":  round(r.avg_weekly_volume, 1),
        "Consistency": r.volume_consistency,
        "Net Score":   r.net_score,
        "_item_id":    r.item_id,
    }
    for r in page_results
])

event = st.dataframe(
    df.drop(columns=["_item_id"]),
    use_container_width=True,
    hide_index=True,
    height=min(600, 35 * len(df) + 38),   # ~35px/row, cap at 600
    column_config={
        "Craft Cost":   st.column_config.NumberColumn(format="%,.0f"),
        "Mall Price":   st.column_config.NumberColumn(format="%,.0f"),
        "Profit":       st.column_config.NumberColumn(format="%,.0f"),
        "Margin %":     st.column_config.NumberColumn(format="%.1f%%"),
        "Avg Vol/wk":   st.column_config.NumberColumn(format="%.1f",
                            help="Average units sold per week over the last 12 weeks"),
        "Consistency":  st.column_config.NumberColumn(format="%.0f%%",
                            help="Weeks with any sales in the last 12 calendar weeks"),
        "Net Score":    st.column_config.NumberColumn(format="%,.0f",
                            help="Profit × Avg Vol/wk — expected weekly earnings"),
    },
    selection_mode="multi-row",
    on_select="rerun",
)

# ── Row actions ────────────────────────────────────────────────────────────────
selected_rows = event.selection.rows if event.selection.rows else []

btn_col1, btn_col2 = st.columns([1, 4])

if len(selected_rows) == 1:
    if btn_col1.button("Full Detail →", use_container_width=True):
        st.session_state.selected_item_id = int(df.iloc[selected_rows[0]]["_item_id"])
        st.switch_page("pages/item.py")

if selected_rows:
    selected_scan = [page_results[i] for i in selected_rows if i < len(page_results)]
    label = (
        f"Verify {len(selected_scan)} selected →"
        if len(selected_rows) < len(page_results)
        else f"Verify all {len(page_results)} →"
    )
    if btn_col2.button(label, type="primary", use_container_width=True):
        st.session_state.verify_queue = selected_scan
        st.switch_page("pages/verify.py")

# ── Inline price chart ─────────────────────────────────────────────────────────
if len(selected_rows) == 1:
    idx     = selected_rows[0]
    row     = page_results[idx]
    item_id = row.item_id
    pd_     = prices.get(item_id)

    history_daily  = pd_.history_daily  if pd_ else []
    history_weekly = pd_.history_weekly if pd_ else []
    history        = history_daily or history_weekly

    st.subheader(f"{row.item_name}")

    # Quick context metrics
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Craft Cost",  f"{row.craft_cost:,.0f}")
    mc2.metric("Mall Price",  f"{row.mall_price:,.0f}")
    mc3.metric("Profit",      f"{row.profit:,.0f}", delta=f"{row.margin_pct:.1f}%")
    mc4.metric("Net Score",   f"{row.net_score:,.0f}")

    if history:
        dates       = [h.date  for h in history]
        hist_prices = [h.price for h in history]
        vol_history = history_weekly or history_daily
        vol_dates   = [h.date   for h in vol_history]
        volumes     = [h.volume for h in vol_history]

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.70, 0.30],
            vertical_spacing=0.04,
            subplot_titles=("Price History", "Weekly Volume"),
        )

        fig.add_trace(go.Scatter(
            x=dates, y=hist_prices,
            name="Pricegun Avg",
            line=dict(color="#4C9BE8", width=2),
            hovertemplate="%{x|%b %d, %Y}<br><b>%{y:,.0f} meat</b><extra></extra>",
        ), row=1, col=1)

        # Craft cost floor
        fig.add_hline(
            y=row.craft_cost, row=1, col=1,
            line_dash="dash", line_color="#e74c3c", line_width=1.5,
            annotation_text=f"Craft cost {row.craft_cost:,.0f}",
            annotation_position="bottom right",
            annotation_font_color="#e74c3c",
        )

        # Current mall price
        fig.add_hline(
            y=row.mall_price, row=1, col=1,
            line_dash="dot", line_color="#2ecc71", line_width=1.5,
            annotation_text=f"Mall price {row.mall_price:,.0f}",
            annotation_position="top right",
            annotation_font_color="#2ecc71",
        )

        # Individual sales if available
        sale_dates  = [s.date       for s in (pd_.sales if pd_ else []) if s.unit_price is not None]
        sale_prices = [s.unit_price for s in (pd_.sales if pd_ else []) if s.unit_price is not None]
        if sale_dates:
            fig.add_trace(go.Scatter(
                x=sale_dates, y=sale_prices,
                mode="markers",
                name="Sales",
                marker=dict(color="#F0A500", size=6, opacity=0.8),
                hovertemplate="%{x|%b %d %H:%M}<br><b>%{y:,.0f} meat</b><extra></extra>",
            ), row=1, col=1)

        fig.add_trace(go.Bar(
            x=vol_dates, y=volumes,
            name="Volume",
            marker_color="#4C9BE8",
            opacity=0.45,
            hovertemplate="%{x|%b %d}<br><b>%{y} sold</b><extra></extra>",
        ), row=2, col=1)

        fig.update_layout(
            height=440,
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(t=40, b=20, l=10, r=10),
        )
        fig.update_yaxes(tickformat=",.0f", title_text="Meat",  row=1, col=1)
        fig.update_yaxes(title_text="Units", row=2, col=1)

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("No price history available for this item.")
