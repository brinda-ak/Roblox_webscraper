"""Search Roblox's public catalog and print basic marketplace metadata."""

import requests

from database import DATABASE_PATH, store_items


CATALOG_SEARCH_URL = "https://catalog.roblox.com/v1/search/items/details"
REQUEST_TIMEOUT_SECONDS = 15


def _json_response(response):
    """Return a JSON response or raise an error that includes useful context."""
    try:
        response.raise_for_status()
        return response.json()
    except requests.exceptions.JSONDecodeError as exc:
        content_type = response.headers.get("content-type", "unknown")
        preview = response.text[:200].replace("\n", " ")
        raise RuntimeError(
            f"Expected JSON but received {content_type}. Response starts: {preview!r}"
        ) from exc


def search_roblox_directory(keyword, category_id=11, limit=10):
    """Search Roblox's catalog and return metadata for up to ``limit`` items.

    The Marketplace details endpoint includes names, prices, and creator data in
    its search response, avoiding a separate request for every result.
    """
    params = {"keyword": keyword, "category": category_id, "limit": limit}
    headers = {"User-Agent": "RobloxCatalogDataCollector/1.0"}

    print(f"Searching the Roblox catalog for: {keyword!r}...")

    try:
        with requests.Session() as session:
            session.headers.update(headers)
            search_response = session.get(
                CATALOG_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS
            )
            search_payload = _json_response(search_response)

            search_results = search_payload.get("data", [])
            if not search_results:
                print("No items found matching that keyword.")
                return []

            items = search_results

    except requests.RequestException as exc:
        print(f"Network/API error while contacting Roblox: {exc}")
        return []
    except RuntimeError as exc:
        print(f"Unexpected Roblox API response: {exc}")
        return []

    stored_count = store_items(items)
    print(f"Saved {stored_count} items to {DATABASE_PATH.name}.")

    print(f"\nFound {len(items)} results:\n" + "=" * 40)
    for item in items:
        price = item.get("price")
        price_text = f"{price} Robux" if price is not None else "Offsale/Free"

        print(f"🎁 {item.get('name', 'Unknown')}")
        print(
            f"   ID: {item.get('id')} | Price: {price_text} | "
            f"Creator: {item.get('creatorName', 'Unknown')}"
        )
        print("-" * 40)

    return items


if __name__ == "__main__":
    search_roblox_directory(keyword="Sword")
