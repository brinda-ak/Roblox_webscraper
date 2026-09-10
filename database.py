"""SQLite persistence for Roblox Marketplace API search responses."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


DATABASE_PATH = Path(__file__).with_name("roblox_marketplace.db")

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS marketplace_items (
    id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    asset_type INTEGER,
    bundle_type TEXT,
    creator_name TEXT,
    creator_target_id INTEGER,
    creator_type TEXT,
    description TEXT,
    favorite_count INTEGER,
    item_name TEXT,
    lowest_price INTEGER,
    price INTEGER,
    price_status TEXT,
    purchase_count INTEGER,
    units_available_for_consumption INTEGER,
    raw_response_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (id, item_type)
);

CREATE TABLE IF NOT EXISTS item_genres (
    item_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    genre TEXT NOT NULL,
    PRIMARY KEY (item_id, item_type, genre),
    FOREIGN KEY (item_id, item_type)
        REFERENCES marketplace_items (id, item_type) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS item_restrictions (
    item_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    restriction TEXT NOT NULL,
    PRIMARY KEY (item_id, item_type, restriction),
    FOREIGN KEY (item_id, item_type)
        REFERENCES marketplace_items (id, item_type) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS item_statuses (
    item_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (item_id, item_type, status),
    FOREIGN KEY (item_id, item_type)
        REFERENCES marketplace_items (id, item_type) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_marketplace_items_name
    ON marketplace_items (item_name);
CREATE INDEX IF NOT EXISTS idx_marketplace_items_creator
    ON marketplace_items (creator_target_id, creator_type);
"""


def initialize_database(database_path=DATABASE_PATH):
    """Create the database and tables if they do not already exist."""
    with sqlite3.connect(database_path) as connection:
        connection.executescript(SCHEMA)


def store_items(items, database_path=DATABASE_PATH):
    """Upsert Marketplace API results and replace their list-field values.

    Returns the number of item responses saved. The complete original response is
    also retained in ``raw_response_json`` so new API fields are not lost.
    """
    now = datetime.now(UTC).isoformat()
    rows = []
    for item in items:
        item_id = item.get("id")
        item_type = item.get("itemType")
        if item_id is None or not item_type:
            continue
        rows.append(
            (
                item_id,
                item_type,
                item.get("assetType"),
                item.get("bundleType"),
                item.get("creatorName"),
                item.get("creatorTargetId"),
                item.get("creatorType"),
                item.get("description"),
                item.get("favoriteCount"),
                item.get("name"),
                item.get("lowestPrice"),
                item.get("price"),
                item.get("priceStatus"),
                item.get("purchaseCount"),
                item.get("unitsAvailableForConsumption"),
                json.dumps(item, separators=(",", ":"), ensure_ascii=False),
                now,
                now,
            )
        )

    initialize_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO marketplace_items (
                id, item_type, asset_type, bundle_type, creator_name,
                creator_target_id, creator_type, description, favorite_count,
                item_name, lowest_price, price, price_status, purchase_count,
                units_available_for_consumption, raw_response_json,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id, item_type) DO UPDATE SET
                asset_type = excluded.asset_type,
                bundle_type = excluded.bundle_type,
                creator_name = excluded.creator_name,
                creator_target_id = excluded.creator_target_id,
                creator_type = excluded.creator_type,
                description = excluded.description,
                favorite_count = excluded.favorite_count,
                item_name = excluded.item_name,
                lowest_price = excluded.lowest_price,
                price = excluded.price,
                price_status = excluded.price_status,
                purchase_count = excluded.purchase_count,
                units_available_for_consumption = excluded.units_available_for_consumption,
                raw_response_json = excluded.raw_response_json,
                last_seen_at = excluded.last_seen_at
            """,
            rows,
        )

        for item in items:
            item_id, item_type = item.get("id"), item.get("itemType")
            if item_id is None or not item_type:
                continue
            for table in ("item_genres", "item_restrictions", "item_statuses"):
                connection.execute(
                    f"DELETE FROM {table} WHERE item_id = ? AND item_type = ?",
                    (item_id, item_type),
                )

            connection.executemany(
                "INSERT INTO item_genres (item_id, item_type, genre) VALUES (?, ?, ?)",
                [(item_id, item_type, genre) for genre in item.get("genres") or []],
            )
            connection.executemany(
                """INSERT INTO item_restrictions (item_id, item_type, restriction)
                   VALUES (?, ?, ?)""",
                [
                    (item_id, item_type, restriction)
                    for restriction in item.get("itemRestrictions") or []
                ],
            )
            connection.executemany(
                "INSERT INTO item_statuses (item_id, item_type, status) VALUES (?, ?, ?)",
                [(item_id, item_type, status) for status in item.get("itemStatus") or []],
            )

    return len(rows)
