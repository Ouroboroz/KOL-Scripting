from __future__ import annotations
from pydantic import BaseModel, Field, field_validator


class Ingredient(BaseModel):
    item_id: int
    quantity: int

    @classmethod
    def from_graphql(cls, node: dict) -> Ingredient:
        return cls(item_id=node["item"], quantity=node["quantity"])


class Concoction(BaseModel):
    id: int
    item_id: int                          # output item ID
    methods: list[str]
    comment: str | None = None
    ingredients: list[Ingredient] = Field(default_factory=list)

    @classmethod
    def from_graphql(cls, node: dict, item_id: int) -> Concoction:
        ingredients = [
            Ingredient.from_graphql(ing)
            for ing in node.get("ingredientsByConcoction", {}).get("nodes", [])
        ]
        return cls(
            id=node["id"],
            item_id=item_id,
            methods=[m.strip() for m in (node.get("methods") or []) if m.strip()],
            comment=node.get("comment"),
            ingredients=ingredients,
        )


class Item(BaseModel):
    id: int
    name: str
    tradeable: bool
    discardable: bool
    autosell: int
    uses: list[str] = Field(default_factory=list)
    concoctions: list[Concoction] = Field(default_factory=list)

    @field_validator("uses", mode="before")
    @classmethod
    def coerce_uses(cls, v):
        return v or []

    @classmethod
    def from_graphql(cls, node: dict) -> Item:
        item_id = node["id"]
        concoctions = [
            Concoction.from_graphql(c, item_id)
            for c in node.get("concoctionsByItem", {}).get("nodes", [])
        ]
        return cls(
            id=item_id,
            name=node["name"],
            tradeable=node.get("tradeable") or False,
            discardable=node.get("discardable") or False,
            autosell=node.get("autosell") or 0,
            uses=node.get("uses") or [],
            concoctions=concoctions,
        )
