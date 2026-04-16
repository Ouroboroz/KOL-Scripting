from __future__ import annotations
import statistics
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


def _parse_decimal(v) -> float | None:
    """Resolve plain numbers, strings, or {"__decimal__": "..."} to float."""
    if v is None:
        return None
    if isinstance(v, dict):
        raw = v.get("__decimal__")
        return float(raw) if raw is not None else None
    return float(v)


class Sale(BaseModel):
    date: datetime
    unit_price: float | None
    quantity: int = 1

    @field_validator("unit_price", mode="before")
    @classmethod
    def parse_unit_price(cls, v):
        return _parse_decimal(v)

    @classmethod
    def from_api(cls, node: dict) -> Sale:
        return cls(
            date=node["date"],
            unit_price=node.get("unitPrice"),
            quantity=node.get("quantity", 1),
        )


class PriceHistoryBucket(BaseModel):
    item_id: int
    date: datetime
    volume: int
    price: float | None

    @field_validator("price", mode="before")
    @classmethod
    def parse_price(cls, v):
        return _parse_decimal(v)

    @classmethod
    def from_api(cls, node: dict) -> PriceHistoryBucket:
        return cls(
            item_id=node["itemId"],
            date=node["date"],
            volume=node.get("volume", 0),
            price=node.get("price"),
        )


class PriceData(BaseModel):
    item_id: int
    name: str = ""
    current_price: float | None     # top-level `value` from API — volume-weighted rolling avg
    volume: int = 0
    history_daily: list[PriceHistoryBucket] = Field(default_factory=list)   # ~2 weeks, daily buckets
    history_weekly: list[PriceHistoryBucket] = Field(default_factory=list)  # ~3 months, weekly buckets
    sales: list[Sale] = Field(default_factory=list)

    @field_validator("current_price", mode="before")
    @classmethod
    def parse_current_price(cls, v):
        return _parse_decimal(v)

    def latest_price(self, n: int = 5) -> float | None:
        """Median of the last n sale prices — robust to bulk buys and outliers.
        Falls back to rolling avg, then last history bucket if no sales available."""
        prices = [s.unit_price for s in self.sales[:n] if s.unit_price is not None]
        if prices:
            return statistics.median(prices)
        if self.current_price is not None:
            return self.current_price
        if self.history_daily:
            return self.history_daily[-1].price
        if self.history_weekly:
            return self.history_weekly[-1].price
        return None

    @classmethod
    def from_api(cls, entry: dict, history_mode: str = "daily") -> "PriceData":
        history = [PriceHistoryBucket.from_api(h) for h in entry.get("history", [])]
        return cls(
            item_id=entry["itemId"],
            name=entry.get("name", ""),
            current_price=entry.get("value"),
            volume=entry.get("volume", 0),
            history_daily=history if history_mode == "daily" else [],
            history_weekly=history if history_mode == "weekly" else [],
            sales=[Sale.from_api(s) for s in entry.get("sales", [])],
        )
