"""
NPC store operations — browse and buy from Kingdom of Loathing's NPC shops.

NPC stores in KoL are accessed via shop.php (the unified NPC shop endpoint
introduced to replace the old per-store PHP files).

  GET  shop.php?whichshop=STORE_ID           → HTML inventory listing
  POST shop.php                               → purchase

The store IDs used here are the same string identifiers used in KolMafia's
npcstores.txt and stored in the ``npc_prices`` table in kol_data's DuckDB.
Examples: "knifegun", "junkshop", "soupkitchen", "armory", "jewelers".

Cross-reference with kol_data
──────────────────────────────
kol_data already fetches and caches NPC prices at build time (from the
KolMafia npcstores.txt on GitHub).  Use those cached prices for arbitrage
calculations.  Use this module when you need to *actually buy* from a store
or verify live stock levels.

Usage
──────
    listings = list_npc_store(session, "junkshop", name_lookup=names)
    result   = buy_npc(session, "junkshop", item_id=207, quantity=3)
"""

from __future__ import annotations

import logging
import re

from lxml import html

from kol_session.session import KoLSession
from kol_client.models import BuyResult, NpcListing

log = logging.getLogger(__name__)

# ── Parsing helpers ───────────────────────────────────────────────────────────

_PRICE_RE   = re.compile(r'([\d,]+)\s+[Mm]eat')
_ITEM_ID_RE = re.compile(r'whichitem=(\d+)|name=["\']whichitem["\']\s+value=["\'](\d+)["\']')
_ROW_ID_RE  = re.compile(r'whichrow=(\d+)')
_QTY_RE     = re.compile(r'(\d+)\s+(?:left|remaining|in stock)', re.IGNORECASE)
# "You acquire …" success signal
_ACQUIRE_RE = re.compile(r'[Yy]ou acquire')


def _parse_int(text: str) -> int:
    return int(text.replace(",", "").strip())


def _parse_store_page(
    page_html: str,
    name_lookup: dict[int, str] | None,
) -> list[NpcListing]:
    """Parse shop.php HTML into a list of NpcListing."""
    tree = html.fromstring(page_html)
    listings: list[NpcListing] = []

    # NPC store rows are typically <tr> elements inside a table.
    # Each buyable row has a form with whichitem and a price cell.
    for form in tree.cssselect("form"):
        form_html = html.tostring(form, encoding="unicode")

        # Item ID — look for hidden whichitem input
        item_id_m = re.search(
            r'name=["\']whichitem["\']\s+value=["\'](\d+)["\']', form_html
        )
        if not item_id_m:
            continue
        item_id = int(item_id_m.group(1))

        # Price
        price_m = _PRICE_RE.search(form.text_content())
        if not price_m:
            continue
        price = _parse_int(price_m.group(1))

        # Row ID — required for purchases via whichrow=; lives in buy links
        row_id: int | None = None
        row_m = _ROW_ID_RE.search(form_html)
        if row_m:
            row_id = int(row_m.group(1))

        # Optional stock quantity
        qty: int | None = None
        qty_m = _QTY_RE.search(form.text_content())
        if qty_m:
            qty = int(qty_m.group(1))

        # Name
        name: str | None = name_lookup.get(item_id) if name_lookup else None
        if name is None:
            # Try to pull from the form label text
            label = form.cssselect("b, .item_name, td b")
            if label:
                name = label[0].text_content().strip() or None

        listings.append(NpcListing(
            item_id=item_id,
            name=name,
            price=price,
            row_id=row_id,
            quantity=qty,
        ))

    return listings


# ── Public API ────────────────────────────────────────────────────────────────

def list_npc_store(
    session: KoLSession,
    store_id: str,
    name_lookup: dict[int, str] | None = None,
) -> list[NpcListing]:
    """Fetch the current inventory of an NPC shop.

    Args:
        session:     Authenticated KoLSession.
        store_id:    KoL store identifier string, e.g. "junkshop", "armory".
                     These match the IDs in KolMafia's npcstores.txt and in
                     kol_data's npc_prices table.
        name_lookup: Optional dict[item_id → name] from kol_data.

    Returns:
        List of NpcListing for every buyable item currently in the store.
        ``quantity`` is None when the shop has unlimited stock (most NPC shops).
    """
    resp = session.get("shop.php", params={"whichshop": store_id})
    resp.raise_for_status()

    listings = _parse_store_page(resp.text, name_lookup)
    log.info("NPC store %r: %d item(s)", store_id, len(listings))
    return listings


def buy_npc(
    session: KoLSession,
    store_id: str,
    item_id: int,
    quantity: int = 1,
    name_lookup: dict[int, str] | None = None,
) -> BuyResult:
    """Buy an item from an NPC shop.

    NPC purchases are simpler than mall purchases — there's no price
    validation needed because NPC prices are fixed.  The server will reject
    the request if the item isn't available (wrong moon sign, locked store,
    etc.) and return an appropriate message.

    Args:
        session:     Authenticated KoLSession.
        store_id:    KoL store identifier string (same as list_npc_store).
        item_id:     Numeric item ID to purchase.
        quantity:    Units to buy.
        name_lookup: Optional dict[item_id → name].

    Returns:
        BuyResult.
    """
    if not session.pwdhash:
        raise RuntimeError("No pwdhash — session must be logged in before buying")

    item_name = name_lookup.get(item_id) if name_lookup else None

    # shop.php requires whichrow (a row ID from the shop listing HTML), not whichitem.
    # Fetch the store page to resolve the row_id for this item.
    listings = list_npc_store(session, store_id, name_lookup=name_lookup)
    listing = next((l for l in listings if l.item_id == item_id), None)
    if listing is None:
        return BuyResult(
            success=False,
            item_id=item_id,
            item_name=item_name,
            quantity_bought=0,
            total_spent=0,
            message=f"Item {item_id} not found in NPC store {store_id!r}",
        )
    if listing.row_id is None:
        return BuyResult(
            success=False,
            item_id=item_id,
            item_name=item_name,
            quantity_bought=0,
            total_spent=0,
            message=f"Could not parse whichrow for item {item_id} in store {store_id!r}",
        )
    item_name = item_name or listing.name

    log.info("buy_npc store=%r item=%d row=%d qty=%d", store_id, item_id, listing.row_id, quantity)

    resp = session.post(
        "shop.php",
        data={
            "whichshop":  store_id,
            "action":     "buyitem",
            "whichrow":   str(listing.row_id),
            "quantity":   str(quantity),
            "ajax":       "1",
            "pwd":        session.pwdhash,
        },
    )
    resp.raise_for_status()
    text = resp.text

    success = bool(_ACQUIRE_RE.search(text))

    # Parse cost from "You spent X Meat" or "X Meat" in the response
    cost_m = re.search(r'spent\s+([\d,]+)\s+[Mm]eat', text)
    if not cost_m:
        cost_m = _PRICE_RE.search(text)

    # Parse quantity acquired
    qty_m = re.search(r'acquire\s+(?:an item:\s+)?[^(]+\((\d+)\)', text, re.IGNORECASE)
    qty_bought = int(qty_m.group(1)) if qty_m else (quantity if success else 0)

    total_spent = 0
    if success and cost_m:
        total_spent = _parse_int(cost_m.group(1))

    return BuyResult(
        success=success,
        item_id=item_id,
        item_name=item_name,
        quantity_bought=qty_bought,
        total_spent=total_spent,
        message=text[:300],
    )
