from __future__ import annotations

import networkx as nx

from kol_data.models.item import Concoction, Item
from kol_data.graph.node_types import item_key, recipe_key, is_item_node


def get_item(G: nx.DiGraph, item_id: int) -> Item:
    return G.nodes[item_key(item_id)]["data"]


def get_recipe(G: nx.DiGraph, concoction_id: int) -> Concoction:
    return G.nodes[recipe_key(concoction_id)]["data"]


def item_ids(G: nx.DiGraph) -> list[int]:
    """Return all item IDs in the graph."""
    return [k[1] for k in G.nodes() if is_item_node(k)]


def get_leaf_ingredients(G: nx.DiGraph, item_id: int) -> set[int]:
    """
    Return all item IDs that are leaf ancestors of `item_id`
    (no crafting recipe — must be purchased).
    """
    start = item_key(item_id)
    ancestors = nx.ancestors(G, start) | {start}
    return {
        k[1]
        for k in ancestors
        if is_item_node(k) and G.in_degree(k) == 0
    }


def find_item(G: nx.DiGraph, query: str) -> int | None:
    """
    Look up an item by numeric ID or name (exact, then substring).
    Returns None if not found or if the substring matches multiple items.
    Use find_items() when you need all substring matches.
    """
    stripped = query.strip()
    if stripped.lstrip("-").isdigit():
        iid = int(stripped)
        return iid if G.has_node(item_key(iid)) else None
    query_lower = stripped.lower()
    for iid in item_ids(G):
        if get_item(G, iid).name.lower() == query_lower:
            return iid
    # Substring fallback — only if unambiguous
    matches = [iid for iid in item_ids(G) if query_lower in get_item(G, iid).name.lower()]
    return matches[0] if len(matches) == 1 else None


def find_items(G: nx.DiGraph, query: str) -> list[tuple[int, str]]:
    """Return all (id, name) pairs whose name contains query (case-insensitive)."""
    stripped = query.strip()
    if stripped.lstrip("-").isdigit():
        iid = int(stripped)
        return [(iid, get_item(G, iid).name)] if G.has_node(item_key(iid)) else []
    query_lower = stripped.lower()
    return [
        (iid, get_item(G, iid).name)
        for iid in item_ids(G)
        if query_lower in get_item(G, iid).name.lower()
    ]
