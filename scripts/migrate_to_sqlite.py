"""
Migrate poi.csv → route_planner/data/poi.db

Usage:
    python scripts/migrate_to_sqlite.py
"""
import csv
import os
import sqlite3

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "route_planner", "data", "poi.csv")
DB_PATH  = os.path.join(os.path.dirname(__file__), "..", "route_planner", "data", "poi.db")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS pois (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    category                TEXT NOT NULL,
    sub_category            TEXT,
    address                 TEXT,
    city                    TEXT,
    area                    TEXT,
    lat                     REAL,
    lng                     REAL,
    rating                  REAL,
    taste_rating            REAL,
    decor_rating            REAL,
    service_rating          REAL,
    hygiene_rating          REAL,
    value_rating            REAL,
    review_count            INTEGER,
    half_year_sales         INTEGER,
    avg_price_per_person    REAL,
    queue_risk              TEXT,
    queue_minutes_peak      INTEGER,
    queue_minutes_offpeak   INTEGER,
    has_group_buy           INTEGER,
    group_buy_title         TEXT,
    group_buy_original_price REAL,
    group_buy_current_price  REAL,
    business_hours          TEXT,
    trend_tag               TEXT,
    recommend_count         INTEGER
)
"""

FIELDS = [
    "id", "name", "category", "sub_category", "address", "city", "area",
    "lat", "lng", "rating", "taste_rating", "decor_rating", "service_rating",
    "hygiene_rating", "value_rating", "review_count", "half_year_sales",
    "avg_price_per_person", "queue_risk", "queue_minutes_peak",
    "queue_minutes_offpeak", "has_group_buy", "group_buy_title",
    "group_buy_original_price", "group_buy_current_price",
    "business_hours", "trend_tag", "recommend_count",
]

REAL_FIELDS = {"lat", "lng", "rating", "taste_rating", "decor_rating", "service_rating",
               "hygiene_rating", "value_rating", "avg_price_per_person",
               "group_buy_original_price", "group_buy_current_price"}
INT_FIELDS  = {"review_count", "half_year_sales", "queue_minutes_peak",
               "queue_minutes_offpeak", "has_group_buy", "recommend_count"}


def _cast(field, value):
    if value == "":
        return None
    if field in REAL_FIELDS:
        return float(value)
    if field in INT_FIELDS:
        return int(value)
    return value


def migrate():
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    conn = sqlite3.connect(DB_PATH)
    conn.execute(CREATE_SQL)
    conn.execute("DELETE FROM pois")

    placeholders = ", ".join("?" * len(FIELDS))
    insert_sql = f"INSERT OR REPLACE INTO pois ({', '.join(FIELDS)}) VALUES ({placeholders})"

    for row in rows:
        values = [_cast(field, row.get(field, "")) for field in FIELDS]
        conn.execute(insert_sql, values)

    conn.commit()
    conn.close()
    print(f"迁移完成：{len(rows)} 条 POI → {DB_PATH}")


if __name__ == "__main__":
    migrate()
