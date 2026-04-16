"""
KoL crafting type / method metadata.

`CraftingType` matches KoLmafia's enum name. Each crafting method string
(e.g. "COOK", "COMBINE", "SMITH") is a key in CRAFTING_METHODS, which maps
to a (CraftingType, description) pair.
"""
from __future__ import annotations

from enum import Enum


class CraftingType(str, Enum):
    """Broad category for a KoL crafting method.

    Values mirror the categories used in KoLmafia's CraftingType enum,
    extended with SEASONAL and SPECIAL for expired/path-locked recipes.
    """
    STANDARD   = "standard"    # always available with no prerequisites
    SKILL      = "skill"       # requires a permanent skill
    EQUIPMENT  = "equipment"   # requires specific equipment / item
    CLASS      = "class"       # class-specific or stat-gated
    SEASONAL   = "seasonal"    # time-limited events (may be expired)
    SPECIAL    = "special"     # path/challenge-path or obscure flags
    UNKNOWN    = "unknown"


# Maps method string → (CraftingType, human-readable description)
CRAFTING_METHODS: dict[str, tuple[CraftingType, str]] = {
    # ── Standard ──────────────────────────────────────────────────────────────
    "COMBINE":          (CraftingType.STANDARD,   "Meat paste combining (10 meat/step)"),
    "COOK":             (CraftingType.STANDARD,   "Basic cooking"),
    "MIX":              (CraftingType.STANDARD,   "Basic cocktail mixing"),
    "SMITH":            (CraftingType.STANDARD,   "Basic smithing"),
    "ROLL":             (CraftingType.STANDARD,   "Rolling pin"),
    "ACOMBINE":         (CraftingType.STANDARD,   "Accessory combining"),
    "SEWER":            (CraftingType.STANDARD,   "Gum-on-a-string sewer"),
    "WAX":              (CraftingType.STANDARD,   "Wax lips crafting"),
    # ── Skill-gated ───────────────────────────────────────────────────────────
    "COOK_FANCY":       (CraftingType.SKILL,      "Advanced Saucecrafting"),
    "MIX_FANCY":        (CraftingType.SKILL,      "Superhuman Cocktailcrafting"),
    "PASTA":            (CraftingType.SKILL,      "Pastamastery (Pastamancer)"),
    "PASTAMASTERY":     (CraftingType.SKILL,      "Pastamastery skill"),
    "REAGENT":          (CraftingType.SKILL,      "Reagent Potion (Sauceror)"),
    "SAUCE":            (CraftingType.SKILL,      "Sauceror saucecrafting"),
    "SSAUCE":           (CraftingType.SKILL,      "Super Advanced Saucecrafting"),
    "DSAUCE":           (CraftingType.SKILL,      "Deep Saucecrafting"),
    "TEMPURA":          (CraftingType.SKILL,      "Tempura batter (Deep Saucecrafting)"),
    "STILL":            (CraftingType.SKILL,      "Nash Crosby's Still"),
    "MALUS":            (CraftingType.SKILL,      "Malus of Forethought (Seal Clubber)"),
    "STAFF":            (CraftingType.SKILL,      "Rodoric the Staffcrafter"),
    "TINKER":           (CraftingType.SKILL,      "Gnomish Tinkering"),
    "WOOL":             (CraftingType.SKILL,      "Spinning wheel"),
    "MUSE":             (CraftingType.SKILL,      "Spirit of the Muse"),
    "ELDRITCH":         (CraftingType.SKILL,      "Eldritch Attunement"),
    "TORSO":            (CraftingType.SKILL,      "Torso Awaregness (Disco Bandit)"),
    "ASMITH":           (CraftingType.SKILL,      "Advanced Smithing"),
    "WSMITH":           (CraftingType.SKILL,      "Weapon Smithing"),
    # ── Equipment-gated ───────────────────────────────────────────────────────
    "JEWEL":            (CraftingType.EQUIPMENT,  "Jewelry making (requires pliers)"),
    "EJEWEL":           (CraftingType.EQUIPMENT,  "Enhanced jewelry (requires pliers)"),
    "HAMMER":           (CraftingType.EQUIPMENT,  "Tenderizing Hammer"),
    "SAUSAGE_O_MATIC":  (CraftingType.EQUIPMENT,  "Sausage-o-Matic™"),
    "TERMINAL":         (CraftingType.EQUIPMENT,  "Source Terminal"),
    "METEOROID":        (CraftingType.EQUIPMENT,  "Meteor shower"),
    "BURNING_LEAVES":   (CraftingType.EQUIPMENT,  "Burning leaves"),
    "PHINEAS":          (CraftingType.EQUIPMENT,  "Phineas"),
    "TIKI":             (CraftingType.EQUIPMENT,  "Tiki Bar"),
    "NEWSPAPER":        (CraftingType.EQUIPMENT,  "Grim Brother's Grimoire"),
    "GRIMACITE":        (CraftingType.EQUIPMENT,  "Grimacite smithing (Grimace moon phase)"),
    # ── Class-specific ────────────────────────────────────────────────────────
    "ACOCK":            (CraftingType.CLASS,      "Advanced Cocktailcrafting (Accordion Thief)"),
    "SACOCK":           (CraftingType.CLASS,      "Super Advanced Cocktailcrafting"),
    "SCOCK":            (CraftingType.CLASS,      "Superhuman Cocktailcrafting"),
    "TNOODLE":          (CraftingType.CLASS,      "Tofurkey Noodle (Pastamancer)"),
    # ── Seasonal / expired ────────────────────────────────────────────────────
    "CRIMBO06":         (CraftingType.SEASONAL,   "Crimbo 2006 — expired"),
    "CRIMBO07":         (CraftingType.SEASONAL,   "Crimbo 2007 — expired"),
    "CRIMBO12":         (CraftingType.SEASONAL,   "Crimbo 2012 — expired"),
    # ── Special / path / obscure ──────────────────────────────────────────────
    "MANUAL":           (CraftingType.SPECIAL,    "Manual use item"),
    "NODISCOVERY":      (CraftingType.SPECIAL,    "Hidden recipe (no discovery)"),
    "NOBEE":            (CraftingType.SPECIAL,    "Bees Hate You path only"),
    "PATENT":           (CraftingType.SPECIAL,    "Patent Medicine"),
    "FEMALE":           (CraftingType.SPECIAL,    "Female-character only"),
    "MALE":             (CraftingType.SPECIAL,    "Male-character only"),
    "WEAPON":           (CraftingType.SPECIAL,    "Weapon-based crafting"),
    "AC":               (CraftingType.SPECIAL,    "AC (unknown)"),
    "SSPD":             (CraftingType.SPECIAL,    "SSPD (unknown)"),
    "SX3":              (CraftingType.SPECIAL,    "SX3 (unknown)"),
    "SUSE":             (CraftingType.SPECIAL,    "Single use"),
    # ROW267–ROW280: specific unlockable recipe rows
    **{f"ROW{n}": (CraftingType.SPECIAL, f"Recipe row {n} unlock") for n in range(267, 281)},
}

# Methods that are permanently expired and should be excluded by default
EXPIRED_METHODS: frozenset[str] = frozenset({"CRIMBO06", "CRIMBO07", "CRIMBO12"})

# Methods excluded by default because their cost model requires external data
# not tracked by the arbitrage calculator (e.g. unlock recipe purchase price)
UNMODELED_METHODS: frozenset[str] = frozenset({"GRIMACITE", "NODISCOVERY"})

# Convenient union: everything ignored in a standard run
DEFAULT_IGNORED_METHODS: frozenset[str] = EXPIRED_METHODS | UNMODELED_METHODS


def crafting_type(method: str) -> CraftingType:
    return CRAFTING_METHODS.get(method, (CraftingType.UNKNOWN, ""))[0]


def crafting_description(method: str) -> str:
    return CRAFTING_METHODS.get(method, (CraftingType.UNKNOWN, method))[1]
