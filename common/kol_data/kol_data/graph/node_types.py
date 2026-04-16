"""
Node key helpers for the KoL bipartite crafting graph.

The graph has two kinds of nodes:
  - Item nodes:   key = ("item", item_id: int)
  - Recipe nodes: key = ("recipe", concoction_id: int)

Edges:
  ("item", ingredient_id) → ("recipe", concoction_id)   [edge attr: quantity]
  ("recipe", concoction_id) → ("item", output_item_id)  [edge attr: none]

Use these helpers everywhere instead of raw tuples so that node format can be
changed in one place.
"""

from __future__ import annotations

type NodeKey = tuple[str, int]


def item_key(item_id: int) -> NodeKey:
    return ("item", item_id)


def recipe_key(concoction_id: int) -> NodeKey:
    return ("recipe", concoction_id)


def is_item_node(key: NodeKey) -> bool:
    return key[0] == "item"


def is_recipe_node(key: NodeKey) -> bool:
    return key[0] == "recipe"


def node_id(key: NodeKey) -> int:
    return key[1]
