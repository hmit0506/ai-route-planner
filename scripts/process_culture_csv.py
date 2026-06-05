#!/usr/bin/env python3
"""
Process culture_step2_aligned_to_poi.csv:
  1. Filter: keep 文化/娱乐/文化娱乐 + sports with 泳灘/郊野公园
  2. Normalize category (文化娱乐→文化, 运动 beaches/parks→自然)
  3. Mock missing fields (decor/service/hygiene/value_rating, review_count, price, queue)
  4. Convert GBK → UTF-8
  5. Output route_planner/data/HK/culture_cleaned.csv (same schema as poi.csv)

Usage:
    PYTHONPATH=. .venv/bin/python3 scripts/process_culture_csv.py
"""
import csv
import hashlib
from pathlib import Path

IN_PATH  = Path("route_planner/data/HK/culture_step2_aligned_to_poi.csv")
OUT_PATH = Path("route_planner/data/HK/culture_cleaned.csv")

# ── Filter ─────────────────────────────────────────────────────────────────
_KEEP_CATS = {"文化", "娱乐", "文化娱乐"}
_KEEP_SPORTS_KW = {"泳灘", "郊野公园"}  # from 运动 category

def _keep(row: dict) -> bool:
    cat = row["category"]
    if cat in _KEEP_CATS:
        return True
    if cat == "运动":
        sub = row.get("sub_category", "")
        return any(kw in sub for kw in _KEEP_SPORTS_KW)
    return False

# ── Category normalization ──────────────────────────────────────────────────
def _normalize_cat(row: dict) -> str:
    cat = row["category"]
    if cat == "文化娱乐":
        return "文化"
    if cat == "运动":
        return "自然"  # beaches / country parks
    return cat  # 文化 / 娱乐 unchanged

# ── avg_price heuristic ────────────────────────────────────────────────────
_FREE_KW  = {"泳灘", "郊野公园", "公園", "海滨共享空间", "主要景点", "湿地公园", "海濱"}
_PAID_MAP = {
    "博物館":  60,
    "表演場地": 120,
}

def _avg_price(row: dict) -> int:
    sub = row.get("sub_category", "")
    for kw, price in _PAID_MAP.items():
        if kw in sub:
            return price
    if any(kw in sub for kw in _FREE_KW):
        return 0
    return 30  # modest default for misc culture venues

# ── Deterministic mock helpers (hashlib so same input → same output) ───────
def _h(poi_id: str) -> int:
    return int(hashlib.md5(poi_id.encode()).hexdigest(), 16)

def _jitter(poi_id: str, base: float, lo: float, hi: float, scale: int = 20) -> float:
    delta = (_h(poi_id) % (scale + 1) - scale // 2) * 0.1
    return round(min(hi, max(lo, base + delta)), 1)

def _mock_ratings(row: dict) -> dict:
    pid   = row["id"]
    base  = float(row.get("rating") or 4.0)
    return {
        "decor_rating":    _jitter(pid + "d", base, 3.0, 5.0),
        "service_rating":  _jitter(pid + "s", base, 3.0, 5.0),
        "hygiene_rating":  _jitter(pid + "h", base, 3.0, 5.0),
        "value_rating":    _jitter(pid + "v", base - 0.2, 2.8, 5.0),
    }

def _mock_queue(row: dict) -> dict:
    pid = row["id"]
    rc  = int(row.get("recommend_count") or 0)
    rat = float(row.get("rating") or 0)
    h   = _h(pid)
    if rc >= 500 and rat >= 4.3:
        return {"queue_risk": "高", "queue_minutes_peak": 15 + h % 16, "queue_minutes_offpeak": 5 + h % 8}
    if rc >= 200 and rat >= 4.0:
        return {"queue_risk": "中", "queue_minutes_peak": 5 + h % 11,  "queue_minutes_offpeak": h % 6}
    return {"queue_risk": "低", "queue_minutes_peak": h % 6, "queue_minutes_offpeak": 0}

def _mock_sales(row: dict) -> int:
    rc = int(row.get("recommend_count") or 0)
    h  = _h(row["id"])
    return rc * 4 + (h % 200)

def _mock_review_count(row: dict) -> int:
    rc = int(row.get("recommend_count") or 0)
    h  = _h(row["id"] + "r")
    multiplier = 2 + (h % 4)   # 2x–5x recommend_count
    return rc * multiplier

# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    with open(IN_PATH, encoding="gbk", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    kept = [r for r in rows if _keep(r)]
    print(f"Kept {len(kept)} / {len(rows)} rows after filtering")

    from collections import Counter
    cats_before = Counter(r["category"] for r in kept)
    print("Categories before normalization:", dict(cats_before))

    out_rows = []
    for row in kept:
        r = dict(row)

        # normalize category
        r["category"] = _normalize_cat(r)

        # mock missing numeric fields
        r.update(_mock_ratings(r))
        r["taste_rating"] = 0          # N/A for culture/nature
        r.update(_mock_queue(r))
        r["review_count"]         = _mock_review_count(r)
        r["half_year_sales"]      = _mock_sales(r)
        r["avg_price_per_person"] = _avg_price(r)

        # no group buy for culture/nature POIs
        r["has_group_buy"]           = 0
        r["group_buy_title"]         = ""
        r["group_buy_original_price"] = 0
        r["group_buy_current_price"]  = 0

        out_rows.append(r)

    cats_after = Counter(r["category"] for r in out_rows)
    print("Categories after normalization:", dict(cats_after))

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"\nOutput → {OUT_PATH}")
    print(f"Total rows: {len(out_rows)}")

    # spot check
    import random; random.seed(1)
    print("\nSample rows:")
    for r in random.sample(out_rows, min(4, len(out_rows))):
        print(f"  [{r['category']}] {r['name'][:20]:<22} "
              f"area={r['area']:<8} price={r['avg_price_per_person']:>3} "
              f"decor={r['decor_rating']} queue={r['queue_risk']}")


if __name__ == "__main__":
    main()
