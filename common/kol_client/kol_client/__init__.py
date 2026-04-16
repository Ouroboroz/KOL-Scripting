"""
kol_client — live Kingdom of Loathing game client.

Depends on kol_session for the authenticated HTTP transport.
Does NOT depend on kol_data — name enrichment is done by the caller
by passing a name_lookup dict built from kol_data.

Quick-start
───────────
    from kol_session import KoLSession
    from kol_client.inventory import MainInventory, Closet
    from kol_client.mall import search_mall, buy_cheapest
    from kol_client.crafting import craft_item
    from kol_client.store import list_npc_store, buy_npc

    with KoLSession.from_env() as session:
        inv = MainInventory()
        inv.refresh(session)
        print(inv.quantity(491))          # how many Dry Noodles do I have?

        result = search_mall(session, "dry noodles")
        print(result.listings[:3])        # three cheapest listings

        buy = buy_cheapest(session, item_id=491, quantity=10, max_price=200)
        print(buy.total_spent)

        craft = craft_item(session, "cook", item1=491, item2=80)  # noodles + sauce
        print(craft.item_name, craft.quantity)
"""

from kol_client.models import (
    BuyResult,
    CraftResult,
    InventoryItem,
    MallListing,
    MallSearchResult,
    NpcListing,
)
from kol_client.inventory import (
    Inventory,
    MainInventory,
    Closet,
    Storage,
    Equipment,
)
from kol_client.mall import (
    search_mall,
    get_store,
    buy_cheapest,
    buy_listing,
    buy_depth,
)
from kol_client.crafting import (
    craft_item,
    multi_use_item,
    CRAFT_TYPES,
)
from kol_client.store import (
    list_npc_store,
    buy_npc,
)

__all__ = [
    # Models
    "BuyResult",
    "CraftResult",
    "InventoryItem",
    "MallListing",
    "MallSearchResult",
    "NpcListing",
    # Inventory
    "Inventory",
    "MainInventory",
    "Closet",
    "Storage",
    "Equipment",
    # Mall
    "search_mall",
    "get_store",
    "buy_cheapest",
    "buy_listing",
    "buy_depth",
    # Crafting
    "craft_item",
    "multi_use_item",
    "CRAFT_TYPES",
    # NPC stores
    "list_npc_store",
    "buy_npc",
]
