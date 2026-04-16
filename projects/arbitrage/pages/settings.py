import tomllib
import tomli_w
import streamlit as st
from pathlib import Path

from kol_data.data import PriceMode
from calculation.config import CraftingConfig
from calculation.loader import load_kol_data, cache_ages

CONFIG_PATH = Path(__file__).parent.parent / "config.toml"

st.title("Settings")

config: CraftingConfig = st.session_state.config

# ── Adventure economics ────────────────────────────────────────────────────────
st.subheader("Adventure Economics")
meat_per_adv = st.number_input(
    "Meat per Adventure",
    min_value=0, value=int(config.meat_per_adventure), step=500,
    help="Your estimated meat value of one adventure. Used to price COOK/MIX/SMITH/STILL steps.",
)
combine_cost = st.number_input(
    "Combine Cost (meat paste)",
    min_value=0, value=int(config.combine_cost), step=1,
    help="Cost of one meat paste. Almost always 10.",
)

st.divider()

# ── Free crafts ────────────────────────────────────────────────────────────────
st.subheader("Free Crafts Today")
st.caption("These crafting steps cost 0 adventures. Consumed greedily from cheapest first.")

col1, col2 = st.columns(2)
free_cooks  = col1.number_input("Free Cooks (COOK)",   min_value=0, value=config.free_cooks,  step=1)
free_mixes  = col2.number_input("Free Mixes (MIX)",    min_value=0, value=config.free_mixes,  step=1)
free_smiths = col1.number_input("Free Smiths (SMITH)", min_value=0, value=config.free_smiths, step=1)
free_stills = col2.number_input("Free Stills (STILL)", min_value=0, value=config.free_stills, step=1)

st.divider()

# ── Unlocked skills / items ────────────────────────────────────────────────────
st.subheader("Unlocked Skills & Items")
has_pliers = st.toggle("Has Pliers (enables JEWELRY crafting)", value=config.has_pliers)
has_malus  = st.toggle("Has Malus (enables MALUS/Pulverize)",  value=config.has_malus)

all_methods = sorted({
    m
    for _, d in st.session_state.kol.graph.nodes(data=True)
    for conc in d.get("concoctions", [])
    for m in conc["methods"]
})
_known_methods = set(all_methods)
ignored_methods = st.multiselect(
    "Ignored Methods",
    options=all_methods,
    default=[m for m in config.ignored_methods if m in _known_methods],
    help="Crafting methods to skip entirely when computing costs.",
)

st.divider()

# ── Apply / Save ───────────────────────────────────────────────────────────────
col_apply, col_save = st.columns(2)

if col_apply.button("Apply to Session", use_container_width=True):
    st.session_state.config = CraftingConfig(
        meat_per_adventure=float(meat_per_adv),
        combine_cost=float(combine_cost),
        free_cooks=int(free_cooks),
        free_mixes=int(free_mixes),
        free_smiths=int(free_smiths),
        free_stills=int(free_stills),
        has_pliers=has_pliers,
        has_malus=has_malus,
        ignored_methods=ignored_methods,
        graph_ttl_hours=config.graph_ttl_hours,
        prices_ttl_hours=config.prices_ttl_hours,
    )
    # Clear cached scan results so next scan uses new config
    st.session_state.pop("scan_results", None)
    st.success("Config applied. Scan results cleared.")

if col_save.button("Save to config.toml", use_container_width=True):
    toml_data = {
        "crafting": {
            "meat_per_adventure": int(meat_per_adv),
            "combine_cost": int(combine_cost),
            "free_cooks": int(free_cooks),
            "free_mixes": int(free_mixes),
            "free_smiths": int(free_smiths),
            "free_stills": int(free_stills),
            "has_pliers": has_pliers,
            "has_malus": has_malus,
            "ignored_methods": ignored_methods,
        },
        "cache": {
            "graph_ttl_hours": int(config.graph_ttl_hours),
            "prices_ttl_hours": int(config.prices_ttl_hours),
        },
    }
    CONFIG_PATH.write_text(tomli_w.dumps(toml_data))
    st.success(f"Saved to {CONFIG_PATH}")

st.divider()

# ── Data management ────────────────────────────────────────────────────────────
st.subheader("Data Management")

ages = cache_ages()

col1, col2 = st.columns(2)
with col1:
    graph_age = ages["graph"]
    graph_label = f"{graph_age:.1f}h ago" if graph_age is not None else "not cached"
    st.metric("Item Graph", graph_label)
    if st.button("Rebuild Graph", use_container_width=True, help="Re-fetch all items from GraphQL (~30s)"):
        with st.spinner("Fetching items from GraphQL..."):
            kol = load_kol_data(st.session_state.config, price_mode=PriceMode.FORCE, force_graph=True)
        st.session_state.kol = kol
        st.session_state.pop("scan_results", None)
        st.success("Graph and prices rebuilt.")
        st.rerun()

with col2:
    prices_age = ages["prices"]
    prices_label = f"{prices_age:.1f}h ago" if prices_age is not None else "not cached"
    st.metric("Prices", prices_label)
    if st.button("Refresh Prices", use_container_width=True, help="Re-fetch prices from Pricegun (~60s)"):
        with st.spinner("Fetching prices from Pricegun..."):
            kol = load_kol_data(st.session_state.config, price_mode=PriceMode.FORCE)
        st.session_state.kol = kol
        st.session_state.pop("scan_results", None)
        st.success("Prices refreshed.")
        st.rerun()
