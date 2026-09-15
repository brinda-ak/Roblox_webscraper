import requests

from database import DATABASE_PATH, store_items


CATALOG_SEARCH_URL = "https://catalog.roblox.com/v1/search/items/details"
REQUEST_TIMEOUT_SECONDS = 15


def _json_response(response):
    """Return JSON or raise an error with useful context."""
    try:
        response.raise_for_status()
        return response.json()

    except requests.exceptions.JSONDecodeError as exc:
        content_type = response.headers.get("content-type", "unknown")
        preview = response.text[:200].replace("\n", " ")

        raise RuntimeError(
            f"Expected JSON but received {content_type}. "
            f"Response starts: {preview!r}"
        ) from exc


def search_roblox_directory(keyword, category_id=11, limit=10):
    """Search Roblox catalog, ordered by all-time sales."""

    params = {
        "keyword": keyword,
        "category": category_id,
        "limit": limit,

        # SORT BY SALES
        "sortType": 2,

        # ALL-TIME SALES
        "sortAggregation": 5,
    }

    headers = {
        "User-Agent": "RobloxCatalogDataCollector/1.0"
    }

    print(f"Searching Roblox for {keyword!r} by popularity (sales)...")

    try:
        with requests.Session() as session:

            session.headers.update(headers)

            response = session.get(
                CATALOG_SEARCH_URL,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS
            )

            payload = _json_response(response)

            items = payload.get("data", [])

            if not items:
                print("No items found.")
                return []

    except requests.RequestException as exc:
        print(f"Network/API error: {exc}")
        return []

    except RuntimeError as exc:
        print(f"Unexpected Roblox API response: {exc}")
        return []

    # Roblox already returned these in sales order
    stored_count = store_items(items)

    print(f"Saved {stored_count} items to {DATABASE_PATH.name}.")

    print(
        f"\nFound {len(items)} results "
        f"(most popular by all-time sales first):\n"
        + "=" * 55
    )

    for rank, item in enumerate(items, start=1):

        price = item.get("price")

        if price is None:
            price_text = item.get("priceStatus", "Offsale/Free")
        else:
            price_text = f"{price} Robux"

        # Roblox may or may not include the actual count
        sales = item.get("purchaseCount")

        print(f"#{rank} 🎁 {item.get('name', 'Unknown')}")

        print(
            f"   ID: {item.get('id')} | "
            f"Price: {price_text} | "
            f"Creator: {item.get('creatorName', 'Unknown')}"
        )

        if sales is not None:
            print(f"   Sales: {sales:,}")

        print("-" * 55)

    return items


if __name__ == "__main__":
    search_roblox_directory(
        keyword="Sword",
        category_id=11,
        limit=10
    )