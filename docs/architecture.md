# KoL Arbitrage Tool — Architecture

## What it does

Finds profitable item crafting opportunities in the Kingdom of Loathing mall.
Given a player's crafting abilities and the current mall price of all tradeable
items, it computes the cheapest way to craft each item and surfaces recipes
where `craft_cost < mall_price`.

---

## Repository layout

```
kol_scripting/                     ← uv workspace root
├── common/
│   └── kol_data/                  ← shared library (game data, graph, DB)
│       └── kol_data/
│           ├── models/
│           │   ├── item.py        ← Item, Concoction, Ingredient (Pydantic v2)
│           │   ├── price.py       ← PriceData, PriceHistoryBucket, Sale
│           │   └── crafting.py    ← CraftingType enum, method metadata, ignore-sets
│           ├── sources/
│           │   ├── graphql.py     ← Fetches items+recipes from data.loathers.net
│           │   └── pricegun.py    ← Fetches mall prices from pricegun.loathers.net
│           ├── graph/
│           │   ├── node_types.py  ← Typed node keys: ("item", id), ("recipe", id)
│           │   ├── builder.py     ← Builds bipartite DiGraph from items+prices
│           │   ├── queries.py     ← get_item(), get_price(), item_ids(), etc.
│           │   └── pricing.py     ← Price helpers
│           ├── db/
│           │   ├── schema.py      ← DuckDB DDL (6 tables)
│           │   └── store.py       ← KolStore: upsert_items(), upsert_prices(), queries
│           ├── cache.py           ← JSON cache with TTL
│           └── __main__.py        ← CLI: build, prices, info commands
│
└── projects/
    └── arbitrage/                 ← application
        ├── calculation/
        │   ├── config.py          ← CraftingConfig (TOML-driven player settings)
        │   └── cost.py            ← Recursive craft cost calculator
        ├── cli/
        │   ├── scan.py            ← Profit scanner (main CLI entrypoint)
        │   └── query.py           ← Per-item cost breakdown + debug flags
        ├── pages/                 ← (future) web UI pages
        └── config.toml            ← Player config
```

---

## Data flow

```
data.loathers.net (GraphQL)
    └─► fetch_all_items()          items + concoctions + ingredients
            └─► build_graph()      bipartite DiGraph (cached as graph.pkl)
                    │
pricegun.loathers.net
    └─► fetch_prices()             PriceData per tradeable item (cached as prices.json)
            └─► attach_prices()    PriceData written onto item nodes in graph
                    │
                    ▼
            compute_crafting_cost()   recursive cost tree per item
                    │
                    ▼
            scan_profitable()         filter + rank by net_score
                    │
                    ▼
            DuckDB (kol.duckdb)       persist items, prices, history, sales
```

---

## Graph model

The recipe graph is a **bipartite directed graph** (NetworkX DiGraph).

Two node types, distinguished by tuple key:

| Node | Key format | Payload |
|---|---|---|
| Item | `("item", item_id)` | `Item` model |
| Recipe | `("recipe", concoction_id)` | `Concoction` model |

Edges:

```
("item", ingredient_id)  →  ("recipe", concoction_id)   [quantity attr]
("recipe", concoction_id)  →  ("item", output_item_id)
```

This makes recipe nodes first-class — you can query "what recipes use this item
as an ingredient" or "what does this recipe produce" without special-casing.

---

## Cost model (`calculation/cost.py`)

`compute_crafting_cost(G, item_id, config)` recursively finds the cheapest way
to produce one unit of `item_id`:

1. For each concoction, check **all methods are available** (AND semantics — they
   are requirements, not alternatives; e.g. `['SMITH', 'GRIMACITE']` means the
   player needs both smithing access AND Grimacite access).
2. Compute method overhead (adventure cost × meat_per_adventure, minus free
   crafts the player has available).
3. For each ingredient, recurse: craft it if that's cheaper than buying.
4. Pick the concoction with the lowest total cost.

**Returns `CraftingCostResult`** with:
- `total_cost` — cheapest craft path
- `buy_cost` — current mall price (what you'd sell for)
- `breakdown` — itemised cost steps
- `chosen_concoction_id` — which recipe won
- `missing_prices` — ingredient IDs with no price data (result discarded if any)

---

## Player config (`config.toml`)

```toml
[crafting]
meat_per_adventure = 4000      # Your opportunity cost per adventure
free_cooks  = 3                # Daily free cooking turns
free_mixes  = 3                # Daily free mixing turns
free_smiths = 0
ignored_methods = [            # Methods you can't use / can't model
    "CRIMBO06", "CRIMBO07", "CRIMBO12",   # expired seasonal
    "GRIMACITE",                           # requires untracked resource
    "NODISCOVERY",                         # requires secret recipe scroll
]

[cache]
graph_ttl_hours  = 24          # Re-fetch item/recipe data once a day
prices_ttl_hours = 1           # Re-fetch mall prices every hour
```

---

## Crafting method semantics

`CraftingType` (in `kol_data/models/crafting.py`) mirrors KoLmafia's naming.
Each method maps to a category (STANDARD, SKILL, EQUIPMENT, SEASONAL, etc.)
and an adventure cost model.

**`DEFAULT_IGNORED_METHODS`** = `EXPIRED_METHODS | UNMODELED_METHODS`:
- `EXPIRED_METHODS` — old seasonal content no longer craftable (CRIMBO06/07/12)
- `UNMODELED_METHODS` — methods whose real cost can't be captured by the model:
  - `GRIMACITE` — requires depleted Grimacite items obtained via secret plans
  - `NODISCOVERY` — requires a secret recipe scroll (one-time cost not tracked)

---

## Market signal columns (scan output)

| Column | Meaning |
|---|---|
| `Net Score` | `profit × weekly_volume` — total meat extractable per week |
| `Trend` | % price change over last 4 weekly buckets (↑ rising, ↓ falling) |
| `Vol%` | Coefficient of variation of price history — higher = more volatile/unstable |
| `SalesConf` | `avg(recent sales) / rolling_avg` — >1 means sales are above avg (price rising or avg lagging); <1 means real price is below avg (stale rolling avg) |

**Interpreting SalesConf:**
- `1.0x` ± 0.1 — market is stable, rolling avg is accurate
- `< 0.8x` — rolling avg is inflated; real margin is worse than displayed
- `> 1.2x` — rolling avg hasn't caught up to recent price rise; real margin may be better

---

## DuckDB persistence

`KolStore` (in `kol_data/db/store.py`) writes to `kol.duckdb` after each build.

**Bulk upsert pattern** (DuckDB doesn't do fast row-by-row ON CONFLICT):
```
BEGIN
CREATE TEMP TABLE _foo_tmp (...)
DELETE FROM _foo_tmp
executemany("INSERT INTO _foo_tmp ...", rows)   ← fast, no conflict check
INSERT INTO foo SELECT * FROM _foo_tmp ON CONFLICT DO UPDATE SET ...
DROP TABLE _foo_tmp
COMMIT
```

Tables: `items`, `concoctions`, `ingredients`, `current_prices`, `price_history`, `sales`

---

## Known data quality issues

- **Depleted Grimacite items**: The upstream GraphQL source (`data.loathers.net`)
  lists some of these with `methods=['SMITH']` only, omitting `GRIMACITE`. The
  comment field `"Depleted Grimacite items, all of which require reading plans"`
  is the reliable signal. These items should be filtered by comment until the
  upstream data is fixed.
