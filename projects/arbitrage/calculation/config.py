from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from kol_data.models.crafting import (  # re-export for convenience
    CraftingType,
    CRAFTING_METHODS,
    EXPIRED_METHODS,
    UNMODELED_METHODS,
    DEFAULT_IGNORED_METHODS,
    crafting_type,
    crafting_description,
)

__all__ = [
    "CraftingType",
    "CRAFTING_METHODS",
    "EXPIRED_METHODS",
    "UNMODELED_METHODS",
    "DEFAULT_IGNORED_METHODS",
    "crafting_type",
    "crafting_description",
    "CraftingConfig",
]


# ── Crafting cost model ────────────────────────────────────────────────────────
# These sets describe KoL game rules used by the arbitrage cost calculator.

# Methods that consume one adventure
_ADV_METHODS = {
    "COOK", "COOK_FANCY",
    "MIX", "MIX_FANCY",
    "SMITH", "ASMITH", "WSMITH",
    "JEWEL", "EJEWEL",
    "STILL",
    "SAUCE", "SSAUCE", "DSAUCE", "REAGENT", "TEMPURA",
    "PASTA", "PASTAMASTERY",
    "STAFF",
    "TINKER",
    "ACOCK", "SACOCK", "SCOCK",
}

_REQUIRES_PLIERS       = {"JEWEL", "EJEWEL"}
_REQUIRES_MALUS        = {"MALUS"}
_REQUIRES_SUPERTINKER  = {"TINKER"}

# Moon sign groupings — determines which perks/stores are accessible
PLUNGER_SIGNS        = frozenset({"mongoose", "wallaby", "vole"})       # Muscle signs → free COMBINE + Degrassi Knoll
CANADIA_SIGNS        = frozenset({"platypus", "opossum", "marmot"})     # Myst signs  → Little Canadia Jewelers
SUPERTINKERING_SIGNS = frozenset({"wombat", "blender", "packrat"})      # Moxie signs → TINKER crafting

# Class → guild store ID (npcstores.txt store_id)
MUSCLE_CLASSES = frozenset({"seal clubber", "turtle tamer"})
MYST_CLASSES   = frozenset({"pastamancer", "sauceror"})
MOXIE_CLASSES  = frozenset({"disco bandit", "accordion thief"})

_CLASS_GUILD_STORE: dict[str, str] = {
    **{cls: "guildstore3" for cls in MUSCLE_CLASSES},   # Smacketeria
    **{cls: "guildstore2" for cls in MYST_CLASSES},     # Gouda's Grimoire and Grocery
    **{cls: "guildstore1" for cls in MOXIE_CLASSES},    # Shadowy Store
}

# Stores accessible to every player — either truly open or quest-gated but
# assumed completed in any normal playthrough.
ALWAYS_ACCESSIBLE_STORES = frozenset({
    # Always available — no requirements
    "madeline",       # Madeline's Baking Supply
    "bugbear",        # Bugbear Bakery
    "bartender",      # The Typical Tavern
    "bartlebys",      # Barrrtleby's Barrrgain Books
    "armory",         # Armory and Leggery
    "nerve",          # Nervewrecker's Store
    "doc",            # Doc Galaktik's Medicine Show
    "tweedle",        # The Tweedleporium
    "meatsmith",      # Meatsmith's Shop
    "generalstore",   # The General Store
    "unclep",         # Uncle P's Antiques
    "snackbar",       # Huggler Memorial Colosseum Snack Bar
    # Quest-gated but assumed completed in any normal playthrough
    "knobdisp",       # The Knob Dispensary (Cobb's Knob quest)
    "whitecitadel",   # White Citadel (after quest)
    "blackmarket",    # The Black Market (after underground quest)
    "hiddentavern",   # The Hidden Tavern (hidden temple unlock)
    "chinatown",      # Chinatown Shops (after Chinatown quest)
})

# Store IDs excluded from NPC price tracking entirely.
_EXCLUDED_STORES = frozenset({
    # Crimbo seasonal — all expired
    "crimbo18", "crimbo18giftomat", "crimbo19",
    "crimbo20cafe", "crimbo20blackmarket",
    "crimbo21cafe", "crimbo21ornaments",
    "crimbo25_cafe",
    # Fallout Shelter — path-specific, hard to access
    "vault1", "vault2", "vault3",
    # Wildfire challenge path — only accessible during that path run
    "wildfire",
    # Unknown / dead content
    "sandpenny",
})

# Methods that are always free (no adventure cost)
_FREE_METHODS = {
    "ACOMBINE", "ROLL", "MALUS", "SUSE", "WAX",
    "MULTI_USE", "SINGLE_USE", "UNEFFECT",
}

# Maps crafting method → the free-use counter it draws from
_FREE_CRAFT_KEYS = {
    "COOK": "free_cooks", "COOK_FANCY": "free_cooks",
    "MIX":  "free_mixes", "MIX_FANCY":  "free_mixes",
    "SMITH": "free_smiths", "ASMITH": "free_smiths", "WSMITH": "free_smiths",
    "STILL": "free_stills",
}


@dataclass
class CraftingConfig:
    """User-specific settings for the arbitrage cost calculator."""
    meat_per_adventure: float = 3000.0
    combine_cost: float = 10.0
    free_cooks: int = 0
    free_mixes: int = 0
    free_smiths: int = 0
    free_stills: int = 0
    has_pliers: bool = False
    has_malus: bool = False
    # Moon sign determines which perks are active automatically.
    # Valid values: mongoose, wallaby, vole, platypus, opossum, marmot, wombat, blender, packrat
    moon_sign: str | None = None
    # Character class determines guild shop access.
    # Valid values: seal clubber, turtle tamer, pastamancer, sauceror, disco bandit, accordion thief
    character_class: str | None = None
    # Explicit overrides — set True to activate regardless of moon sign
    has_plunger: bool = False        # free infinite COMBINE (auto-active for Muscle signs)
    has_supertinkering: bool = False # unlocks TINKER crafting (auto-active for Moxie signs)
    # Hippy Store war outcome: "none" | "hippy" | "fratboy" | "both"
    hippy_store: str = "none"
    # IotM-gated stores
    has_chateau: bool = False        # Chateau Mantegna Gift Shop (chateau)
    has_mayo_clinic: bool = False    # The Mayo Clinic (mayoclinic)
    has_clan_fireworks: bool = False # Clan Underground Fireworks Shop (fwshop)
    has_hack_market: bool = False    # Hack Market — yearly IotY (cyber_hackmarket)
    # Drip Institute — requires completing The Drip content
    drip_done: bool = False          # Drip Institute Cafeteria + Armory (dripcafeteria, driparmory)
    ignored_methods: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORED_METHODS))
    # item_id -> human-readable reason why this item is excluded from scan results
    ignored_items: dict[int, str] = field(default_factory=dict)

    graph_ttl_hours: float = 24.0
    prices_ttl_hours: float = 1.0

    # ── Derived properties from moon sign ─────────────────────────────────────

    @property
    def plunger_active(self) -> bool:
        """True if free infinite COMBINE is available (Muscle sign or explicit override)."""
        return self.has_plunger or (self.moon_sign or "").lower() in PLUNGER_SIGNS

    @property
    def supertinkering_active(self) -> bool:
        """True if TINKER crafting is available (Moxie sign or explicit override)."""
        return self.has_supertinkering or (self.moon_sign or "").lower() in SUPERTINKERING_SIGNS

    @property
    def accessible_store_ids(self) -> set[str]:
        """NPC store IDs the player can buy from, based on sign/class/IotM/quest flags."""
        stores: set[str] = set(ALWAYS_ACCESSIBLE_STORES)
        # Moon sign gates
        sign = (self.moon_sign or "").lower()
        if sign in CANADIA_SIGNS:
            stores.add("jewelers")       # Little Canadia Jewelers
        if sign in PLUNGER_SIGNS:
            stores.add("gnoll")          # Degrassi Knoll Bakery and Hardware
            stores.add("gnomart")        # Gno-Mart
        # Guild by class
        cls = (self.character_class or "").lower()
        guild = _CLASS_GUILD_STORE.get(cls)
        if guild:
            stores.add(guild)
        # Hippy Store — war outcome controls which virtual store_id is accessible
        if self.hippy_store in ("hippy", "both"):
            stores.add("hippy_hippy")
        if self.hippy_store in ("fratboy", "both"):
            stores.add("hippy_fratboy")
        # IotM gates
        if self.has_chateau:        stores.add("chateau")
        if self.has_mayo_clinic:    stores.add("mayoclinic")
        if self.has_clan_fireworks: stores.add("fwshop")
        if self.has_hack_market:    stores.add("cyber_hackmarket")
        # Drip Institute content
        if self.drip_done:
            stores.add("dripcafeteria")
            stores.add("driparmory")
        return stores

    @classmethod
    def from_toml(cls, path: Path) -> CraftingConfig:
        data = tomllib.loads(path.read_text())
        crafting = data.get("crafting", {})
        cache = data.get("cache", {})
        raw_ignored = crafting.get("ignored_items", {})
        raw_sign = crafting.get("moon_sign")
        raw_class = crafting.get("character_class")
        return cls(
            meat_per_adventure=float(crafting.get("meat_per_adventure", 3000)),
            combine_cost=float(crafting.get("combine_cost", 10)),
            free_cooks=int(crafting.get("free_cooks", 0)),
            free_mixes=int(crafting.get("free_mixes", 0)),
            free_smiths=int(crafting.get("free_smiths", 0)),
            free_stills=int(crafting.get("free_stills", 0)),
            has_pliers=bool(crafting.get("has_pliers", False)),
            has_malus=bool(crafting.get("has_malus", False)),
            moon_sign=str(raw_sign).lower() if raw_sign else None,
            character_class=str(raw_class).lower() if raw_class else None,
            has_plunger=bool(crafting.get("has_plunger", False)),
            has_supertinkering=bool(crafting.get("has_supertinkering", False)),
            hippy_store=str(crafting.get("hippy_store", "none")).lower(),
            has_chateau=bool(crafting.get("has_chateau", False)),
            has_mayo_clinic=bool(crafting.get("has_mayo_clinic", False)),
            has_clan_fireworks=bool(crafting.get("has_clan_fireworks", False)),
            has_hack_market=bool(crafting.get("has_hack_market", False)),
            drip_done=bool(crafting.get("drip_done", False)),
            ignored_methods=list(crafting.get("ignored_methods", list(DEFAULT_IGNORED_METHODS))),
            ignored_items={int(k): str(v) for k, v in raw_ignored.items()},
            graph_ttl_hours=float(cache.get("graph_ttl_hours", 24)),
            prices_ttl_hours=float(cache.get("prices_ttl_hours", 1)),
        )

    def is_method_available(self, method: str) -> bool:
        if method in self.ignored_methods:
            return False
        if method in _REQUIRES_PLIERS and not self.has_pliers:
            return False
        if method in _REQUIRES_MALUS and not self.has_malus:
            return False
        if method in _REQUIRES_SUPERTINKER and not self.supertinkering_active:
            return False
        return True

    def adventure_cost(self, method: str, used_free: dict[str, int]) -> float | None:
        if not self.is_method_available(method):
            return None
        if method == "COMBINE":
            return 0.0 if self.plunger_active else self.combine_cost
        if method in _FREE_METHODS:
            return 0.0
        if method in _ADV_METHODS:
            key = _FREE_CRAFT_KEYS.get(method)
            if key and used_free.get(key, 0) < getattr(self, key):
                used_free[key] = used_free.get(key, 0) + 1
                return 0.0
            return float(self.meat_per_adventure)
        return 0.0  # unknown method — treat as free

    def fresh_used_free(self) -> dict[str, int]:
        return {"free_cooks": 0, "free_mixes": 0, "free_smiths": 0, "free_stills": 0}
