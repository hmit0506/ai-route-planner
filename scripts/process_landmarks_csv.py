#!/usr/bin/env python3
"""
Process hk_missing_landmarks_amap_aligned_to_poi.csv:
  1. Skip duplicate POIs already in poi.csv
  2. Normalize category: 文化娱乐 → 文化
  3. Convert area + sub_category Simplified → Traditional (OpenCC)
  4. Fix business_hours: collapse repeated entries to first slot
  5. Mock missing numeric fields
  6. Output route_planner/data/HK/landmarks_cleaned.csv

Usage:
    PYTHONPATH=. .venv/bin/python3 scripts/process_landmarks_csv.py
"""
import csv
import hashlib
import re
from pathlib import Path

import opencc

IN_PATH  = Path("route_planner/data/HK/hk_missing_landmarks_amap_aligned_to_poi.csv")
POI_PATH = Path("route_planner/data/poi.csv")
OUT_PATH = Path("route_planner/data/HK/landmarks_cleaned.csv")

_S2T = opencc.OpenCC("s2t")

# ── Dedup ──────────────────────────────────────────────────────────────────
def _load_existing_names() -> set:
    with open(POI_PATH, encoding="utf-8") as f:
        return {r["name"] for r in csv.DictReader(f)}

# ── Category normalization ─────────────────────────────────────────────────
def _normalize_cat(cat: str) -> str:
    return "文化" if cat == "文化娱乐" else cat  # 文化/娱乐 unchanged

# ── Business hours cleanup ─────────────────────────────────────────────────
_BIZ_SLOT = re.compile(r"\d{1,2}:\d{2}-\d{1,2}:\d{2}")

def _clean_biz(hours: str) -> str:
    slots = _BIZ_SLOT.findall(hours)
    if not slots:
        return hours
    # Deduplicate while preserving order, keep first unique slot
    seen = dict.fromkeys(slots)
    return slots[0] if len(seen) == 1 else " / ".join(list(seen)[:2])

# ── avg_price by sub_category (Traditional Chinese after conversion) ────────
_PRICE_MAP = {
    "主題公園": 400,
    "觀光纜車": 180,
    "大學校園": 0,
    "旅遊景點": 0,
    "宗教古蹟": 0,
    "歷史建築": 30,
    "觀景地標": 0,
    "創意街區": 0,
    "城市地標": 0,
}

def _avg_price(sub_trad: str) -> int:
    return _PRICE_MAP.get(sub_trad, 0)

# ── Deterministic mock helpers ─────────────────────────────────────────────
def _h(poi_id: str) -> int:
    return int(hashlib.md5(poi_id.encode()).hexdigest(), 16)

def _jitter(poi_id: str, base: float, lo: float, hi: float, scale: int = 20) -> float:
    delta = (_h(poi_id) % (scale + 1) - scale // 2) * 0.1
    return round(min(hi, max(lo, base + delta)), 1)

def _mock_ratings(row: dict) -> dict:
    pid  = row["id"]
    base = float(row.get("rating") or 4.0)
    return {
        "decor_rating":   _jitter(pid + "d", base, 3.0, 5.0),
        "service_rating": _jitter(pid + "s", base, 3.0, 5.0),
        "hygiene_rating": _jitter(pid + "h", base, 3.0, 5.0),
        "value_rating":   _jitter(pid + "v", base - 0.2, 2.8, 5.0),
    }

def _mock_queue(row: dict) -> dict:
    pid = row["id"]
    rc  = int(row.get("recommend_count") or 0)
    rat = float(row.get("rating") or 0)
    h   = _h(pid)
    if rc >= 500 and rat >= 4.3:
        return {"queue_risk": "高", "queue_minutes_peak": 15 + h % 16, "queue_minutes_offpeak": 5 + h % 8}
    if rc >= 200 and rat >= 4.0:
        return {"queue_risk": "中", "queue_minutes_peak": 5 + h % 11, "queue_minutes_offpeak": h % 6}
    return {"queue_risk": "低", "queue_minutes_peak": h % 6, "queue_minutes_offpeak": 0}

def _mock_sales(row: dict) -> int:
    rc = int(row.get("recommend_count") or 0)
    return rc * 4 + (_h(row["id"]) % 200)

def _mock_review_count(row: dict) -> int:
    rc = int(row.get("recommend_count") or 0)
    multiplier = 2 + (_h(row["id"] + "r") % 4)
    return rc * multiplier

# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    existing_names = _load_existing_names()

    with open(IN_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    print(f"Input rows: {len(rows)}")

    out_rows = []
    skipped = []
    seen_names: set = set()
    for row in rows:
        r = dict(row)

        # 1. Simplified → Traditional first (needed for correct dedup)
        for field in ("name", "address", "sub_category", "area", "trend_tag"):
            r[field] = _S2T.convert(r[field])

        # 2. Skip duplicates (against poi.csv and within this file)
        if r["name"] in existing_names or r["name"] in seen_names:
            skipped.append(r["name"])
            continue
        seen_names.add(r["name"])

        # 3. Normalize category
        r["category"] = _normalize_cat(r["category"])

        # 4. Fix business_hours
        r["business_hours"] = _clean_biz(r["business_hours"])

        # 5. Mock numeric fields
        r.update(_mock_ratings(r))
        r["taste_rating"]        = 0
        r.update(_mock_queue(r))
        r["review_count"]        = _mock_review_count(r)
        r["half_year_sales"]     = _mock_sales(r)
        r["avg_price_per_person"] = _avg_price(r["sub_category"])

        # 6. No group buy for landmarks
        r["has_group_buy"]            = 0
        r["group_buy_title"]          = ""
        r["group_buy_original_price"] = 0
        r["group_buy_current_price"]  = 0

        out_rows.append(r)

    print(f"Skipped (duplicates): {skipped}")
    print(f"Output rows: {len(out_rows)}")

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"\nOutput → {OUT_PATH}")

    import random; random.seed(42)
    print("\nSample rows:")
    for r in random.sample(out_rows, min(5, len(out_rows))):
        print(f"  [{r['category']}] {r['name'][:22]:<24} "
              f"area={r['area']:<8} sub={r['sub_category']:<8} "
              f"price={r['avg_price_per_person']:>3} queue={r['queue_risk']}")

    # Verify business_hours fixed
    print("\nBusiness hours sample (fixed):")
    for r in out_rows:
        if len(r["business_hours"]) > 20:
            print(f"  {r['name']}: {r['business_hours']}")

if __name__ == "__main__":
    main()
