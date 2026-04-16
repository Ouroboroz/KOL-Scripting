"""
Entry point for the KoL Arbitrage web UI.
Run with: uv run streamlit run projects/arbitrage/app.py
"""

import logging
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # load KOL_USERNAME / KOL_PASSWORD from .env if present

from kol_data.data import PriceMode
from calculation.config import CraftingConfig
from calculation.loader import load_kol_data

logging.basicConfig(level=logging.WARNING)

CONFIG_PATH = Path(__file__).parent / "config.toml"

st.set_page_config(
    page_title="KoL Arbitrage",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _init():
    if "initialized" not in st.session_state:
        config = CraftingConfig.from_toml(CONFIG_PATH) if CONFIG_PATH.exists() else CraftingConfig()
        st.session_state.config = config
        with st.spinner("Loading item graph and prices..."):
            kol = load_kol_data(config, price_mode=PriceMode.AUTO)
        st.session_state.kol = kol
        st.session_state.initialized = True


_init()

pg = st.navigation(
    {
        "Analysis": [
            st.Page("pages/scan.py",   title="Profitable Crafts",  icon="💰", default=True),
            st.Page("pages/item.py",   title="Item Detail",        icon="🔍"),
            st.Page("pages/verify.py", title="Live Verification",  icon="📡"),
        ],
        "Config": [
            st.Page("pages/settings.py", title="Settings", icon="⚙️"),
        ],
    }
)
pg.run()
