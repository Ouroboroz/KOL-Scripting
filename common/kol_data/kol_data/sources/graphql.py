import logging
import time
import httpx

from kol_data.models.item import Item

log = logging.getLogger(__name__)

ENDPOINT = "https://data.loathers.net/graphql"

QUERY = """
query GetItems($first: Int!, $after: Cursor) {
  allItems(first: $first, after: $after) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      name
      tradeable
      discardable
      autosell
      uses
      concoctionsByItem {
        nodes {
          id
          methods
          comment
          ingredientsByConcoction {
            nodes {
              quantity
              item
              itemByItem {
                id
                name
                tradeable
                autosell
              }
            }
          }
        }
      }
    }
  }
}
"""


def _post_with_retry(client: httpx.Client, payload: dict, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            resp = client.post(ENDPOINT, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            log.warning("Request failed (%s), retrying in %ds...", exc, wait)
            time.sleep(wait)
    raise RuntimeError("Unreachable")


def fetch_all_items(page_size: int = 500) -> list[Item]:
    items: list[Item] = []
    cursor = None
    page = 0

    with httpx.Client() as client:
        while True:
            variables: dict = {"first": page_size}
            if cursor:
                variables["after"] = cursor

            data = _post_with_retry(client, {"query": QUERY, "variables": variables})
            page_data = data["data"]["allItems"]
            nodes = page_data["nodes"]

            for node in nodes:
                items.append(Item.from_graphql(node))

            page += 1
            log.info("Page %d: fetched %d items (total: %d)", page, len(nodes), len(items))

            page_info = page_data["pageInfo"]
            if not page_info["hasNextPage"]:
                break
            cursor = page_info["endCursor"]

    log.info("Fetched %d items total", len(items))
    return items
