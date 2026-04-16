"""
Mall operations — search, inspect stores, and purchase items.

Read operations (no pwdhash needed)
────────────────────────────────────
    search_mall(session, "dry noodles")          # all shops carrying the item
    get_store(session, store_id=123456)           # full inventory of one shop

Write operations (attach session.pwdhash)
──────────────────────────────────────────
    buy_cheapest(session, item_id=491, quantity=5, max_price=10_000)
        → buys from the cheapest listing(s) until quantity filled,
          skipping any listing above max_price

    buy_listing(session, store_id=123456, item_id=491, quantity=2, expected_price=9500)
        → buys from a specific known listing;
          KoL rejects the purchase if the price has changed since you looked

HTML parsing
────────────
KoL's mall pages are HTML, not JSON. We use lxml for speed and correctness.
The key patterns:

  Search results page (mall.php?pudnuggler=...):
    <table class="item">                     ← one per shop
      <tr> ... shop name / player ... </tr>
      <tr> ... price / qty / limit  ... </tr>
    </table>

  Store page (mallstore.php?whichstore=ID):
    <table class="item">                     ← one per item in that shop
      similar structure
    </table>

  The "Buy" action (mallstore.php POST):
    whichstore=ID, buying=1, whichitem=ITEM_ID, howmany=QTY, pwd=HASH
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

from lxml import html

from kol_session.session import KoLSession
from kol_client.models import BuyResult, MallListing, MallSearchResult

log = logging.getLogger(__name__)

_DEBUG_DUMP_PATH = "/tmp/kol_mall_debug.html"


def _dump_debug_html(query: str, page: int, url: object, html: str) -> None:
    """Write the full response to /tmp/kol_mall_debug.html for inspection."""
    try:
        with open(_DEBUG_DUMP_PATH, "w") as f:
            f.write(html)
        log.debug(
            "No listings parsed for %r (page %d). URL=%s. "
            "Full HTML written to %s",
            query, page, url, _DEBUG_DUMP_PATH,
        )
    except OSError:
        log.debug(
            "No listings parsed for %r (page %d). URL=%s. First 2000 chars:\n%s",
            query, page, url, html[:2000],
        )

# ── Regex / parse helpers ─────────────────────────────────────────────────────

# "3,200 Meat" or "3200 Meat"
_PRICE_RE = re.compile(r'([\d,]+)\s+Meat')
# "limit N per day"
_LIMIT_RE = re.compile(r'limit\s+(\d+)\s+per\s+day', re.IGNORECASE)
# "searchprice=11000000" in store link href (fallback for price)
_SEARCHPRICE_RE = re.compile(r'searchprice=(\d+)')
# Row IDs: "stock_{store_id}_{item_id}"
_STOCK_ROW_RE = re.compile(r'^stock_(\d+)_(\d+)$')


def _parse_int(text: str) -> int:
    return int(text.replace(",", "").strip())


# ── HTML parsers ──────────────────────────────────────────────────────────────

def _parse_search_page(
    page_html: str,
    item_name_hint: str | None,
    name_lookup: dict[int, str] | None,
) -> list[MallListing]:
    """Parse one page of mall.php search results into a list of MallListing.

    KoL's search results page uses:
      <table class="itemtable">
        <tr id="item_{item_id}">           ← item header row (skip)
        <tr class="blackabove graybelow">  ← column headers (skip)
        <tr id="stock_{store_id}_{item_id}">  ← one per store listing
          <td/>                    spacer
          <td class="store">...</td>
          <td class="stock">{qty}</td>
          <td class="small">{limit or &nbsp;}</td>
          <td class="price">{price} Meat</td>
          <td class="buyers">...</td>
        </tr>
      </table>
    """
    tree = html.fromstring(page_html)
    listings: list[MallListing] = []

    for row in tree.cssselect("tr[id]"):
        row_id = row.get("id", "")
        m = _STOCK_ROW_RE.match(row_id)
        if not m:
            continue

        store_id = int(m.group(1))
        item_id  = int(m.group(2))

        # Store name from td.store > a > b
        store_name = "Unknown"
        store_b = row.cssselect("td.store a b")
        if store_b:
            store_name = store_b[0].text_content().strip()

        # Stock from td.stock
        qty = 0
        stock_els = row.cssselect("td.stock")
        if stock_els:
            try:
                qty = _parse_int(stock_els[0].text_content())
            except (ValueError, AttributeError):
                qty = 0

        # Price from td.price text ("11,000,000 Meat")
        unit_price: int | None = None
        price_els = row.cssselect("td.price")
        if price_els:
            pm = _PRICE_RE.search(price_els[0].text_content())
            if pm:
                unit_price = _parse_int(pm.group(1))

        # Fallback: searchprice= in the store link href
        if unit_price is None:
            store_a = row.cssselect("td.store a")
            if store_a:
                sp_m = _SEARCHPRICE_RE.search(store_a[0].get("href", ""))
                if sp_m:
                    unit_price = int(sp_m.group(1))

        if unit_price is None:
            log.debug("Could not parse price for store %d item %d — skipping", store_id, item_id)
            continue

        # Limit: 4th td (index 3) — "limit N per day" or whitespace
        limit: int | None = None
        tds = row.findall("td")
        if len(tds) >= 4:
            limit_m = _LIMIT_RE.search(tds[3].text_content())
            if limit_m:
                limit = int(limit_m.group(1))

        # Item name
        item_name: str | None = None
        if name_lookup:
            item_name = name_lookup.get(item_id)
        if item_name is None:
            item_name = item_name_hint

        listings.append(MallListing(
            store_id=store_id,
            store_name=store_name,
            player_name="Unknown",   # not present in search results HTML
            item_id=item_id,
            item_name=item_name,
            unit_price=unit_price,
            quantity=qty,
            limit_per_day=limit,
        ))

    return listings


def _parse_store_page(
    page_html: str,
    store_id: int,
    store_name_hint: str | None,
    player_name_hint: str | None,
    name_lookup: dict[int, str] | None,
) -> list[MallListing]:
    """Parse a mallstore.php page into a list of MallListing (one per item).

    mallstore.php uses the same itemtable / stock_{store_id}_{item_id} row
    structure as the search results page, so we re-use _parse_search_page and
    fill in the store context from the page header.
    """
    # Parse listings using the same row structure
    listings = _parse_search_page(page_html, None, name_lookup)
    if not listings:
        return []

    # Resolve store name and player name from page header
    tree = html.fromstring(page_html)
    store_name = store_name_hint or "Unknown"
    player_name = player_name_hint or "Unknown"

    # Store name is usually in an <h1> or similar near the top
    for sel in ("h1", "h2", ".storename", "b"):
        els = tree.cssselect(sel)
        if els:
            candidate = els[0].text_content().strip()
            if candidate:
                store_name = candidate
                break

    # Player name in showplayer.php links
    player_m = re.search(
        r'showplayer\.php\?who=\d+[^>]*>([^<]+)<',
        page_html,
    )
    if player_m:
        player_name = player_m.group(1).strip()

    # Patch store context into every listing (search page doesn't know)
    for listing in listings:
        listing.store_id   = store_id
        listing.store_name = store_name
        listing.player_name = player_name

    return listings


# ── Order book depth analysis ─────────────────────────────────────────────────

def buy_depth(
    listings: list[MallListing],
    units_needed: int,
) -> tuple[float, bool]:
    """Walk the order book cheapest-first to acquire ``units_needed`` units.

    Respects ``limit_per_day`` on each listing.  Listings are re-sorted by
    unit_price so callers don't need to pre-sort.

    Args:
        listings:     List of MallListing (typically from search_mall).
        units_needed: How many units you want to buy.

    Returns:
        ``(avg_price_per_unit, can_fill)`` where ``avg_price_per_unit`` is the
        weighted average cost per unit across the units actually filled, and
        ``can_fill`` is True when the order book can supply all ``units_needed``.
        Returns ``(0.0, False)`` if listings is empty or units_needed <= 0.
    """
    if not listings or units_needed <= 0:
        return 0.0, False

    remaining = units_needed
    total_cost = 0.0

    for listing in sorted(listings, key=lambda l: l.unit_price):
        if remaining <= 0:
            break
        available = listing.quantity
        if listing.limit_per_day is not None:
            available = min(available, listing.limit_per_day)
        to_buy = min(remaining, available)
        if to_buy <= 0:
            continue
        total_cost += to_buy * listing.unit_price
        remaining -= to_buy

    units_bought = units_needed - remaining
    if units_bought == 0:
        return 0.0, False

    avg_price = total_cost / units_bought
    return avg_price, remaining == 0


# ── Public API ────────────────────────────────────────────────────────────────

def search_mall(
    session: KoLSession,
    query: str,
    max_pages: int = 5,
    name_lookup: dict[int, str] | None = None,
) -> MallSearchResult:
    """Search the mall for an item by name or item ID.

    Paginates automatically up to ``max_pages`` result pages (10 stores/page).
    Listings are returned sorted cheapest-first.

    Args:
        session:     Authenticated KoLSession.
        query:       Item name substring or numeric item ID as a string.
        max_pages:   Maximum pages to fetch (each page has up to 10 store entries).
        name_lookup: Optional dict[item_id → name] from kol_data for name enrichment.

    Returns:
        MallSearchResult with all listings found.
    """
    all_listings: list[MallListing] = []
    pages_fetched = 0

    for page in range(max_pages):
        params = {
            "pudnuggler": query,
            "category": "allitems",
            "start": page * 10,
        }
        resp = session.get("mall.php", params=params)
        resp.raise_for_status()
        page_html = resp.text

        # KoL redirects to a single-item page when the search is unambiguous —
        # detect that by checking if we're on a mallstore page instead of mall.php
        if "mallstore.php" in str(resp.url) or "whichstore=" in page_html[:500]:
            # Landed on a store page — same row structure, reuse the parser
            listings = _parse_search_page(page_html, query, name_lookup)
            all_listings.extend(listings)
            pages_fetched += 1
            break

        page_listings = _parse_search_page(page_html, query, name_lookup)
        pages_fetched += 1

        if not page_listings:
            _dump_debug_html(query, page, resp.url, page_html)
            break   # no more results

        all_listings.extend(page_listings)

        # Stop early if this page wasn't full (fewer than 10 → last page)
        if len(page_listings) < 10:
            break

    # Sort cheapest first
    all_listings.sort(key=lambda l: l.unit_price)

    # Resolve item identity from the listing set
    item_id: int | None = None
    item_name: str | None = None
    if all_listings:
        # All listings should be for the same item — take the first non-zero
        ids = [l.item_id for l in all_listings if l.item_id]
        if ids:
            item_id = ids[0]
        names = [l.item_name for l in all_listings if l.item_name]
        if names:
            item_name = names[0]

    log.info(
        "Mall search %r: %d listings across %d page(s)",
        query, len(all_listings), pages_fetched,
    )

    return MallSearchResult(
        query=query,
        item_id=item_id,
        item_name=item_name,
        listings=all_listings,
        pages_fetched=pages_fetched,
    )


def get_store(
    session: KoLSession,
    store_id: int,
    name_lookup: dict[int, str] | None = None,
) -> list[MallListing]:
    """Fetch the full item listing of a specific mall shop.

    Args:
        session:     Authenticated KoLSession.
        store_id:    Numeric KoL store ID.
        name_lookup: Optional dict[item_id → name].

    Returns:
        List of MallListing, one per item the shop carries.
    """
    resp = session.get("mallstore.php", params={"whichstore": store_id})
    resp.raise_for_status()

    listings = _parse_store_page(resp.text, store_id, None, None, name_lookup)
    log.info("Store %d: %d item(s)", store_id, len(listings))
    return listings


def buy_cheapest(
    session: KoLSession,
    item_id: int,
    quantity: int,
    max_price: int | None = None,
    name_lookup: dict[int, str] | None = None,
) -> BuyResult:
    """Buy ``quantity`` units of an item from the cheapest available listings.

    Walks listings cheapest-first and purchases from each until the desired
    quantity is filled.  Skips any listing priced above ``max_price``.

    Args:
        session:     Authenticated KoLSession.
        item_id:     Numeric item ID to purchase.
        quantity:    Total units to acquire.
        max_price:   Per-unit price ceiling (inclusive).  None = no limit.
        name_lookup: Optional dict[item_id → name].

    Returns:
        BuyResult summarising total units bought and Meat spent.
    """
    if not session.pwdhash:
        raise RuntimeError("No pwdhash — session must be logged in before buying")

    item_name = name_lookup.get(item_id) if name_lookup else None

    # Search using the item ID as the query string — KoL accepts numeric IDs
    result = search_mall(session, str(item_id), name_lookup=name_lookup)

    if not result.listings:
        return BuyResult(
            success=False,
            item_id=item_id,
            item_name=item_name,
            quantity_bought=0,
            total_spent=0,
            message="No listings found",
        )

    remaining = quantity
    total_spent = 0
    total_bought = 0
    messages: list[str] = []

    for listing in result.listings:
        if remaining <= 0:
            break
        if max_price is not None and listing.unit_price > max_price:
            log.debug(
                "Listing from store %d at %d Meat exceeds max_price %d — stopping",
                listing.store_id, listing.unit_price, max_price,
            )
            break

        to_buy = min(remaining, listing.quantity)
        if listing.limit_per_day is not None:
            to_buy = min(to_buy, listing.limit_per_day)
        if to_buy <= 0:
            continue

        buy_result = buy_listing(
            session,
            store_id=listing.store_id,
            item_id=item_id,
            quantity=to_buy,
            expected_price=listing.unit_price,
            name_lookup=name_lookup,
        )
        messages.append(buy_result.message)

        if buy_result.success:
            total_bought += buy_result.quantity_bought
            total_spent  += buy_result.total_spent
            remaining    -= buy_result.quantity_bought
        else:
            log.warning(
                "Purchase from store %d failed: %s",
                listing.store_id, buy_result.message,
            )

    success = total_bought >= quantity
    return BuyResult(
        success=success,
        item_id=item_id,
        item_name=item_name,
        quantity_bought=total_bought,
        total_spent=total_spent,
        message=" | ".join(messages) if messages else "No purchases attempted",
    )


def buy_listing(
    session: KoLSession,
    store_id: int,
    item_id: int,
    quantity: int,
    expected_price: int,
    name_lookup: dict[int, str] | None = None,
) -> BuyResult:
    """Buy from a specific mall listing at a specific expected price.

    KoL validates the ``price`` field server-side — if the store owner has
    changed the price since you last saw it, the purchase is rejected.  This
    prevents accidentally paying more than you intended.

    Args:
        session:        Authenticated KoLSession.
        store_id:       Numeric store ID.
        item_id:        Numeric item ID.
        quantity:       Units to buy.
        expected_price: Per-unit price you expect to pay.
        name_lookup:    Optional dict[item_id → name].

    Returns:
        BuyResult.
    """
    if not session.pwdhash:
        raise RuntimeError("No pwdhash — session must be logged in before buying")

    item_name = name_lookup.get(item_id) if name_lookup else None

    # KoL requires whichitem in the format "{item_id}.{price}" — the price is
    # embedded in the item field so the server can reject stale-price purchases.
    resp = session.post(
        "mallstore.php",
        data={
            "buying":      "1",
            "ajax":        "1",
            "whichstore":  str(store_id),
            "whichitem":   f"{item_id}.{expected_price}",
            "quantity":    str(quantity),
            "pwd":         session.pwdhash,
        },
    )
    resp.raise_for_status()
    text = resp.text

    # KoL returns "You acquire..." on success, "You can't afford" etc. on failure.
    success = "You acquire" in text or "acquire an item" in text.lower()

    cost_m = re.search(r'([\d,]+)\s+[Mm]eat', text)
    total_spent = _parse_int(cost_m.group(1)) if cost_m else (expected_price * quantity)

    qty_m = re.search(r'acquire\s+(\d+)', text, re.IGNORECASE)
    qty_bought = int(qty_m.group(1)) if qty_m else (quantity if success else 0)

    log.info(
        "buy_listing store=%d item=%d qty=%d price=%d → success=%s",
        store_id, item_id, quantity, expected_price, success,
    )

    return BuyResult(
        success=success,
        item_id=item_id,
        item_name=item_name,
        quantity_bought=qty_bought,
        total_spent=total_spent if success else 0,
        message=text[:300],
    )
