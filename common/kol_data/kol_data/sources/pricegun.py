import logging
import httpx

from kol_data.models.price import PriceData, PriceHistoryBucket

log = logging.getLogger(__name__)

BASE_URL = "https://pricegun.loathers.net/api"


def _fetch_chunks(
    client: httpx.Client,
    chunks: list[list[int]],
    history_mode: str,
) -> dict[int, list[dict]]:
    """Fetch raw API entries for each chunk, keyed by item_id."""
    raw: dict[int, dict] = {}
    for idx, chunk in enumerate(chunks):
        url = f"{BASE_URL}/{','.join(str(i) for i in chunk)}?history={history_mode}"
        log.debug("Chunk %d/%d (%s)", idx + 1, len(chunks), history_mode)
        try:
            resp = client.get(url)
        except httpx.HTTPError as exc:
            log.warning("Request error for chunk %d (%s): %s", idx + 1, history_mode, exc)
            continue
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        payload = resp.json()
        for entry in (payload if isinstance(payload, list) else [payload]):
            if "error" not in entry:
                raw[entry["itemId"]] = entry
    return raw


def fetch_prices(
    item_ids: list[int],
    tradeable_ids: set[int],
    chunk_size: int = 50,
) -> dict[int, PriceData]:
    """
    Fetch price data for all tradeable item IDs.
    Makes two passes (daily + weekly) and stores each history series separately.
    Returns {item_id: PriceData}.
    """
    ids_to_fetch = [i for i in item_ids if i in tradeable_ids]
    log.info("Fetching prices for %d tradeable items", len(ids_to_fetch))
    chunks = [ids_to_fetch[i:i + chunk_size] for i in range(0, len(ids_to_fetch), chunk_size)]

    with httpx.Client(timeout=30) as client:
        # Daily pass: value, sales, and daily history
        daily_raw = _fetch_chunks(client, chunks, "daily")
        results: dict[int, PriceData] = {
            item_id: PriceData.from_api(entry, history_mode="daily")
            for item_id, entry in daily_raw.items()
        }

        # Weekly pass: longer-term history only (merge into existing PriceData)
        weekly_raw = _fetch_chunks(client, chunks, "weekly")
        for item_id, entry in weekly_raw.items():
            weekly_history = [PriceHistoryBucket.from_api(h) for h in entry.get("history", [])]
            if item_id in results:
                results[item_id].history_weekly = weekly_history
            else:
                # Item only appeared in weekly pass (shouldn't happen, but handle gracefully)
                results[item_id] = PriceData.from_api(entry, history_mode="weekly")

    log.info("Got price data for %d items", len(results))
    return results
