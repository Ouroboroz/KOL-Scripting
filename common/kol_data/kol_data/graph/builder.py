import logging
import networkx as nx

from kol_data.models.item import Item
from kol_data.graph.node_types import item_key, recipe_key

log = logging.getLogger(__name__)


def build_graph(items: list[Item]) -> nx.DiGraph:
    """
    Build a bipartite directed crafting graph.

    Node types
    ----------
    ("item", item_id)         -- stores Item under "data"
    ("recipe", concoction_id) -- stores Concoction under "data"

    Edges
    -----
    ("item", ing_id) → ("recipe", conc_id)   attr: quantity
    ("recipe", conc_id) → ("item", item_id)  (output link, no attrs)
    """
    G = nx.DiGraph()

    # Add all item nodes
    for item in items:
        G.add_node(item_key(item.id), data=item)

    # Add recipe nodes + edges
    for item in items:
        out_key = item_key(item.id)
        for conc in item.concoctions:
            rec_key = recipe_key(conc.id)
            G.add_node(rec_key, data=conc)
            # Recipe → output item
            G.add_edge(rec_key, out_key)
            # Ingredient items → recipe
            for ing in conc.ingredients:
                ing_key = item_key(ing.item_id)
                if not G.has_node(ing_key):
                    log.debug("Ingredient %d not in graph, skipping edge", ing.item_id)
                    continue
                G.add_edge(ing_key, rec_key, quantity=ing.quantity)

    log.info(
        "Graph built: %d item nodes, %d recipe nodes, %d edges",
        sum(1 for k in G.nodes() if k[0] == "item"),
        sum(1 for k in G.nodes() if k[0] == "recipe"),
        G.number_of_edges(),
    )
    return G
