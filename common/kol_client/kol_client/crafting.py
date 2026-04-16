"""
Crafting operations for Kingdom of Loathing.

Two distinct mechanics
──────────────────────
1. craft_item()      — combine two (or one) ingredients into a new item.
                       Covers all standard crafting types via craft.php.
                       Supports batching (quantity > 1).

2. multi_use_item()  — use N copies of the same item for a combined effect.
                       This is NOT the same as crafting: it calls inv_use.php
                       with action=multi and a count.  Some items produce
                       different results when used in bulk vs. singly.

Crafting types (craft_type parameter)
──────────────────────────────────────
  "combine"    Meatpaste / Meatsmithing without a hammer (COMBINE)
  "cook"       Oven / hot plate cooking (COOK)
  "cocktail"   Cocktailcrafting (MIX)
  "smith"      Innabox / Meatsmithing with a hammer (SMITH)
  "jewelry"    Jewelry-making pliers (JEWELRY)
  "still"      Nash Crosby's Still (STILL)
  "malus"      Malus of Forethought (MALUS)
  "staff"      Chefstaff creation (STAFF)
  "sushi"      Sushi-rolling mat (SUSHI)
  "sugar"      Sugar sheet folding (SUGAR)

The strings above match KoL's craft.php ``type=`` POST field.
They also map 1:1 to the method names in kol_data Concoction.methods
(modulo capitalisation — kol_data stores them uppercased).

Response parsing
────────────────
craft.php returns an HTML page.  On success it contains "You acquire" or
"acquire an item"; on failure it contains "don't have" or "You need" etc.
We parse the output item ID and quantity where possible, but fall back to
reporting success=True/False and whatever the raw message says.
"""

from __future__ import annotations

import logging
import re

from kol_session.session import KoLSession
from kol_client.models import CraftResult

log = logging.getLogger(__name__)

# ── Response parsing helpers ──────────────────────────────────────────────────

# "You acquire an item: Dry Noodles (3)"  or  "You acquire Dry Noodles"
_ACQUIRE_RE   = re.compile(r'[Yy]ou acquire (?:an item: )?([^(<\n]+?)(?:\s*\((\d+)\))?(?:<|$)')
# whichitem hidden field in the result page: <input … name="whichitem" value="491">
_ITEM_ID_RE   = re.compile(r'name=["\']whichitem["\']\s+value=["\'](\d+)["\']')
# "You don't have enough …" / "You need …" etc.
_FAIL_PHRASES = ("don't have", "need ", "can't ", "cannot ", "You need", "already used")


def _parse_craft_response(text: str, name_lookup: dict[int, str] | None) -> CraftResult:
    """Extract item, quantity, and success flag from craft.php HTML."""
    success = (
        "You acquire" in text
        or "acquire an item" in text.lower()
    )
    if not success and any(p in text for p in _FAIL_PHRASES):
        return CraftResult(
            success=False,
            item_id=None,
            item_name=None,
            quantity=0,
            message=text[:300],
        )

    item_id: int | None = None
    item_name: str | None = None
    quantity = 1

    id_m = _ITEM_ID_RE.search(text)
    if id_m:
        item_id = int(id_m.group(1))
        if name_lookup:
            item_name = name_lookup.get(item_id)

    acq_m = _ACQUIRE_RE.search(text)
    if acq_m:
        if item_name is None:
            item_name = acq_m.group(1).strip()
        if acq_m.group(2):
            quantity = int(acq_m.group(2))

    return CraftResult(
        success=success,
        item_id=item_id,
        item_name=item_name,
        quantity=quantity,
        message=text[:300],
    )


# ── Public API ────────────────────────────────────────────────────────────────

#: Map from human-friendly craft type names to KoL's craft.php ``type=`` values.
#: Also accepts the raw KoL strings directly (they're the same here).
CRAFT_TYPES: dict[str, str] = {
    "combine":  "combine",
    "cook":     "cook",
    "cocktail": "cocktail",
    "smith":    "smith",
    "jewelry":  "jewelry",
    "still":    "still",
    "malus":    "malus",
    "staff":    "staff",
    "sushi":    "sushi",
    "sugar":    "sugar",
}

# kol_data stores methods uppercase — provide a lookup so callers can pass
# either "COOK" (from kol_data) or "cook" (KoL endpoint value)
_KD_TO_CRAFT: dict[str, str] = {k.upper(): v for k, v in CRAFT_TYPES.items()}


def _resolve_craft_type(craft_type: str) -> str:
    """Accept either 'cook' or 'COOK'; return the endpoint value."""
    lower = craft_type.lower()
    if lower in CRAFT_TYPES:
        return CRAFT_TYPES[lower]
    upper = craft_type.upper()
    if upper in _KD_TO_CRAFT:
        return _KD_TO_CRAFT[upper]
    raise ValueError(
        f"Unknown craft_type {craft_type!r}. "
        f"Valid values: {sorted(CRAFT_TYPES)}"
    )


def craft_item(
    session: KoLSession,
    craft_type: str,
    item1: int,
    item2: int | None = None,
    quantity: int = 1,
    name_lookup: dict[int, str] | None = None,
) -> CraftResult:
    """Craft an item using two (or one) ingredients.

    Args:
        session:     Authenticated KoLSession.
        craft_type:  Crafting method — e.g. "cook", "smith", "cocktail".
                     Accepts both lowercase endpoint names ("cook") and
                     kol_data uppercase method names ("COOK").
        item1:       Item ID of the first ingredient.
        item2:       Item ID of the second ingredient.  Pass None for
                     single-ingredient recipes.
        quantity:    Number of batches to craft (KoL's ``qty`` field).
        name_lookup: Optional dict[item_id → name] for enriching the result.

    Returns:
        CraftResult with success status, output item info, and raw message.
    """
    if not session.pwdhash:
        raise RuntimeError("No pwdhash — session must be logged in before crafting")

    kol_type = _resolve_craft_type(craft_type)

    data: dict[str, str] = {
        "action":   "craft",
        "mode":     kol_type,
        "qty":      str(quantity),
        "a":        str(item1),
        "pwd":      session.pwdhash,
    }
    if item2 is not None:
        data["b"] = str(item2)

    log.info(
        "craft_item type=%s item1=%d item2=%s qty=%d",
        kol_type, item1, item2, quantity,
    )

    resp = session.post("craft.php", data=data)
    resp.raise_for_status()

    result = _parse_craft_response(resp.text, name_lookup)
    # Scale reported quantity by batch count when server doesn't tell us
    if result.success and result.quantity == 1 and quantity > 1:
        result.quantity = quantity

    return result


def multi_use_item(
    session: KoLSession,
    item_id: int,
    quantity: int,
    name_lookup: dict[int, str] | None = None,
) -> CraftResult:
    """Use multiple copies of the same item at once (multi-use mechanic).

    This is distinct from crafting — it calls ``multiuse.php`` with
    ``action=useitem``.  Some items produce special results when used in bulk
    (e.g. ten-leaf clovers, certain food/drink items with stacking effects).

    Args:
        session:     Authenticated KoLSession.
        item_id:     Item ID to multi-use.
        quantity:    Number of copies to use simultaneously.
        name_lookup: Optional dict[item_id → name].

    Returns:
        CraftResult describing what was produced/consumed.
    """
    if not session.pwdhash:
        raise RuntimeError("No pwdhash — session must be logged in before using items")

    log.info("multi_use_item item_id=%d quantity=%d", item_id, quantity)

    resp = session.post(
        "multiuse.php",
        data={
            "action":    "useitem",
            "ajax":      "1",
            "whichitem": str(item_id),
            "quantity":  str(quantity),
            "pwd":       session.pwdhash,
        },
    )
    resp.raise_for_status()

    result = _parse_craft_response(resp.text, name_lookup)

    # If no output item was parsed, the "output" is the side-effect itself
    # (stat gains, buffs, etc.) — still report the consumed item info
    if result.item_id is None and name_lookup:
        result.item_name = name_lookup.get(item_id, result.item_name)

    return result
