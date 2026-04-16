# KoL Arbitrage Tool — Midterm Roadmap

## Guiding principles

- **Don't sweep the mall.** A few targeted lookups per run is fine and
  consistent with how every KoLmafia script works. Full sweeps risk ToS issues
  and aren't necessary for our use case.
- **Pricegun surfaces candidates; live mall confirms execution.** Use the cheap
  aggregate data to find opportunities, the order book only to verify before acting.
- **Model decay, not just opportunity.** The goal isn't just to find a margin —
  it's to find one that will still exist when you execute, and to know when to stop.

---

## Phase 2 — Targeted order book queries

**Goal:** Validate that a profitable item's margin is real before acting on it.
Pricegun gives rolling averages; the mall order book tells you what you can
actually buy right now and at what depth.

### What to build

**`kol_data/sources/mall.py`** — `MallSession` class:

```python
class MallSession:
    """Authenticated KoL session for targeted mall queries."""

    @classmethod
    def login(cls, username: str, password: str) -> MallSession:
        # POST https://www.kingdomofloathing.com/login.php
        # Store session cookies
        ...

    def get_order_book(self, item_id: int, limit: int = 20) -> list[MallListing]:
        # GET mall.php?action=searchmall&whichitem=<id>&num=<limit>&ajax=1
        # Returns [(store_id, store_name, quantity, price), ...]
        ...

    def close(self) -> None: ...
    def __enter__ / __exit__: ...
```

**`MallListing` model:**
```python
@dataclass
class MallListing:
    store_id: int
    store_name: str
    quantity: int
    unit_price: int
```

**Depth analysis:**
```python
def buy_depth(listings: list[MallListing], units_needed: int) -> tuple[float, bool]:
    """
    Returns (avg_price_per_unit, can_fill).
    Walks the order book cheapest-first to buy `units_needed`.
    """
```

### How it fits into the scan

After `scan_profitable()` returns the top N results, run a second pass on the
top 20 (or filtered candidates):

```
For each candidate:
  1. Query order book for output item → can_fill_output, real_sell_price
  2. Query order book for each ingredient → can_fill_inputs, real_input_cost
  3. Recompute profit with real prices
  4. Add to ScanResult: real_profit, real_margin, depth_verified=True/False
```

Items where `depth_verified=False` (can't fill at the assumed price) get
flagged or dropped from the final output.

### Session management

- Login once at scan startup, reuse cookies across all queries.
- Store credentials in env vars (`KOL_USERNAME`, `KOL_PASSWORD`), never in config.toml.
- Respect a minimum delay between requests (~2-5s) to avoid rate limiting.
- Graceful re-login on session expiry (KoL sessions time out after ~1 hour idle).

---

## Phase 3 — Competitive pressure model

**Goal:** Track each opportunity over time. Know when a margin is structural
vs. temporary, and when someone else has started competing you out.

### Opportunity state machine

```python
class OpportunityState(str, Enum):
    WATCHING    = "watching"    # Identified, not yet confirmed
    CONFIRMED   = "confirmed"   # Stable margin + depth verified over N runs
    COMPETING   = "competing"   # Margin shrinking — someone else in the trade
    SATURATED   = "saturated"   # Margin gone, stop
    RECOVERING  = "recovering"  # Was saturated, price recovering — re-evaluate
```

### What drives state transitions

| Transition | Signal |
|---|---|
| WATCHING → CONFIRMED | margin stable for 3+ runs, sales_conf ≈ 1.0, depth verified |
| CONFIRMED → COMPETING | margin shrinking >10%/week for 2 consecutive scans |
| CONFIRMED → COMPETING | input prices rising while output price holds |
| COMPETING → SATURATED | margin < min_profit threshold |
| SATURATED → RECOVERING | output price rising >20% over 2 weeks |
| RECOVERING → CONFIRMED | margin re-established, depth verified |

### Signals to track per item per run

Store in DuckDB `opportunity_log` table:

```sql
CREATE TABLE opportunity_log (
    item_id      INTEGER,
    scanned_at   TIMESTAMPTZ,
    craft_cost   DOUBLE,
    mall_price   DOUBLE,
    profit       DOUBLE,
    margin_pct   DOUBLE,
    volume       INTEGER,
    price_trend  DOUBLE,
    sales_conf   DOUBLE,
    depth_ok     BOOLEAN,
    state        TEXT,
    PRIMARY KEY (item_id, scanned_at)
);
```

This gives you a time series per item — you can query "show me the margin
trajectory for long pork lasagna over the last 30 days."

---

## Phase 4 — Crafting order generation

**Goal:** Given a confirmed, depth-verified opportunity, produce an actionable
crafting plan: what to buy, how much, what to craft, and what to list at.

### Order model

```python
@dataclass
class CraftingOrder:
    item_id: int
    item_name: str
    units_to_craft: int

    # Inputs
    ingredient_buys: list[IngredientBuy]   # what to buy from mall

    # Execution
    crafting_method: str
    adventures_needed: int

    # Output
    list_price: int    # undercut current lowest by 1 meat
    expected_profit: int
    expected_roi_pct: float
```

### Volume sizing

Don't craft more than the market can absorb:
- `units_to_craft = min(weekly_volume * 0.3, max_batch_size)`
- Crafting more than ~30% of weekly volume risks moving the price against yourself.
- `max_batch_size` is configurable (default: 50 units per run).

### Listing price

- Pull current order book for output item.
- List at `(lowest_ask - 1)` to be first in queue.
- If you're already the lowest ask (from a previous run), leave price alone.

---

## Phase 5 — Strategy change detection

**Goal:** Automatically know when to stop, pause, or switch strategies.

### Stop conditions

| Condition | Action |
|---|---|
| Margin falls below `min_profit` | Mark SATURATED, stop crafting |
| Input item no longer available at model price | Flag, re-evaluate next scan |
| Output item volume drops >50% | Reduce batch size or pause |
| You've accumulated >N unsold units | Pause new crafting runs |

### Rotation logic

When an item goes SATURATED, automatically promote the next-best CONFIRMED
item from the scan results. Maintain a "portfolio" of 3-5 active items rather
than betting everything on one.

---

## Implementation order

| Priority | Task | Complexity |
|---|---|---|
| Now | Fix depleted Grimacite filter (comment-based) | Trivial |
| Now | Add `--filter-confirmed` flag to scan (stable trend + sales_conf 0.9-1.1) | Small |
| Phase 2 | `MallSession` + `get_order_book()` | Medium |
| Phase 2 | Depth-aware cost recomputation in scan | Medium |
| Phase 3 | `opportunity_log` table + state machine | Medium |
| Phase 3 | Margin decay / competitive pressure alerts | Medium |
| Phase 4 | `CraftingOrder` generation + volume sizing | Medium |
| Phase 4 | Listing price logic | Small |
| Phase 5 | Stop conditions + portfolio rotation | Large |

---

## What we're deliberately NOT building (yet)

- **Full mall sweeping** — not needed, ToS gray area, Pricegun already does it better.
- **Automated mall buying/selling** — requires careful rate limiting, session
  management, and error handling. Build the analysis layer first; execution comes last.
- **Price manipulation detection** — interesting but complex. The `SalesConf`
  column already gives an early signal for prices that look manipulated (very
  high conf with a sudden spike in trend).
