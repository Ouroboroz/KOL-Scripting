import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from kol_data.graph.queries import get_item
from calculation.cost import compute_crafting_cost
from calculation.loader import find_item

st.title("Item Detail")

kol = st.session_state.kol
config = st.session_state.config
G = kol.graph
prices = kol.prices

# ── Item search ────────────────────────────────────────────────────────────────
default_query = str(st.session_state.get("selected_item_id", ""))
query = st.text_input("Item name or ID", value=default_query, placeholder="e.g. dry noodles or 435")

if not query:
    st.info("Enter an item name or ID above.")
    st.stop()

item_id = find_item(G, query)
if item_id is None:
    st.error(f"Item not found: **{query}**")
    st.stop()

item = get_item(G, item_id)
price_data = prices.get(item_id)
result = compute_crafting_cost(G, prices, item_id, config)

# ── Header metrics ─────────────────────────────────────────────────────────────
st.subheader(f"{item.name} (#{item_id})")

col1, col2, col3, col4 = st.columns(4)
buy = result.buy_cost
craft = result.total_cost
profit = (buy - craft) if (buy is not None and craft is not None) else None

col1.metric("Mall Price", f"{buy:,.0f}" if buy else "N/A")
col2.metric("Craft Cost", f"{craft:,.0f}" if craft else "N/A")
col3.metric(
    "Profit",
    f"{profit:,.0f}" if profit is not None else "N/A",
    delta=f"{(profit / buy * 100):.1f}%" if (profit and buy) else None,
)
col4.metric("Tradeable", "Yes" if item.tradeable else "No")

if result.recipe_comment:
    st.info(f"Recipe note: {result.recipe_comment}")

st.divider()

# ── Price chart ────────────────────────────────────────────────────────────────
history_daily = price_data.history_daily if price_data else []
history_weekly = price_data.history_weekly if price_data else []
sales = price_data.sales if price_data else []

# Use daily for price line (finer), weekly for volume bars (longer range)
history = history_daily or history_weekly

if history:
    dates = [h.date for h in history]
    hist_prices = [h.price for h in history]
    vol_history = history_weekly or history_daily
    volumes = [h.volume for h in vol_history]
    vol_dates = [h.date for h in vol_history]

    sale_dates = [s.date for s in sales if s.unit_price is not None]
    sale_prices = [s.unit_price for s in sales if s.unit_price is not None]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.04,
        subplot_titles=("Price History", "Weekly Volume"),
    )

    fig.add_trace(go.Scatter(
        x=dates, y=hist_prices,
        name="Weekly Avg",
        line=dict(color="#4C9BE8", width=2),
        hovertemplate="%{x|%b %d, %Y}<br><b>%{y:,.0f} meat</b><extra></extra>",
    ), row=1, col=1)

    if sale_dates:
        fig.add_trace(go.Scatter(
            x=sale_dates, y=sale_prices,
            mode="markers",
            name="Sales",
            marker=dict(color="#F0A500", size=7, opacity=0.8, symbol="circle"),
            hovertemplate="%{x|%b %d %H:%M}<br><b>%{y:,.0f} meat</b><extra></extra>",
        ), row=1, col=1)

    if craft is not None:
        fig.add_hline(
            y=craft, row=1, col=1,
            line_dash="dash", line_color="#E84C4C", line_width=1.5,
            annotation_text=f"Craft cost: {craft:,.0f}",
            annotation_position="top left",
            annotation_font_color="#E84C4C",
        )

    fig.add_trace(go.Bar(
        x=vol_dates, y=volumes,
        name="Volume",
        marker_color="#4C9BE8",
        opacity=0.5,
        hovertemplate="%{x|%b %d}<br><b>%{y} sold</b><extra></extra>",
    ), row=2, col=1)

    fig.update_layout(
        height=520,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=40, b=20, l=10, r=10),
    )
    fig.update_yaxes(tickformat=",.0f", title_text="Meat", row=1, col=1)
    fig.update_yaxes(title_text="Units", row=2, col=1)

    st.plotly_chart(fig, use_container_width=True)
else:
    st.warning("No price history available for this item.")

st.divider()

# ── Crafting breakdown ─────────────────────────────────────────────────────────
st.subheader("Crafting Breakdown")

if result.unavailable:
    st.error("Cannot be crafted with current config settings.")
elif result.breakdown:
    ing_steps = [s for s in result.breakdown if s.source != "overhead"]
    overhead_steps = [s for s in result.breakdown if s.source == "overhead"]

    if ing_steps:
        ing_df = pd.DataFrame([
            {
                "Ingredient": s.item_name,
                "Qty": s.quantity,
                "Unit Cost": s.unit_cost,
                "Subtotal": s.unit_cost * s.quantity,
                "Source": s.source,
            }
            for s in ing_steps
        ])
        st.dataframe(
            ing_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Unit Cost": st.column_config.NumberColumn(format="%,.0f"),
                "Subtotal":  st.column_config.NumberColumn(format="%,.0f"),
            },
        )

    if overhead_steps:
        st.caption("Crafting overhead")
        for step in overhead_steps:
            label = f"**{step.method}** step"
            cost_str = "free (used free craft)" if step.method_overhead == 0 else f"{step.method_overhead:,.0f} meat"
            st.write(f"- {label}: {cost_str}")

    if result.missing_prices:
        st.warning(f"Missing prices for item IDs: {result.missing_prices}")
else:
    st.info("No crafting recipe available — buy only.")

# ── Item metadata ──────────────────────────────────────────────────────────────
with st.expander("Item metadata"):
    st.json({
        "id": item_id,
        "name": item.name,
        "tradeable": item.tradeable,
        "discardable": item.discardable,
        "autosell": item.autosell,
        "uses": item.uses,
        "concoctions": len(item.concoctions),
    })
