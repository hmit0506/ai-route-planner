import os
import sqlite3
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "poi.db")


def _query_category(
    city: str,
    area: str,
    category: str,
    pref_sub_categories: list,   # food_pref for 餐饮, culture_pref for 文化
    avoid_sub_categories: list,
    budget_pp: float,
    limit: int = 10,
) -> list[dict]:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row

    city_pat = f"%{city}%" if city else "%"
    area_pat = f"%{area}%" if area else "%"
    price_cap = budget_pp * 1.2

    # Build preference ORDER BY: matching sub_categories sort first
    pref_cases = " ".join(
        f"WHEN sub_category LIKE '%{p}%' THEN {i}" for i, p in enumerate(pref_sub_categories)
    )
    pref_order = f"CASE {pref_cases} ELSE {len(pref_sub_categories)} END," if pref_cases else ""

    # Build avoid exclusion
    if avoid_sub_categories:
        placeholders = ",".join("?" * len(avoid_sub_categories))
        avoid_clause = f"AND sub_category NOT IN ({placeholders})"
        avoid_params = avoid_sub_categories
    else:
        avoid_clause = ""
        avoid_params = []

    sql = f"""
        SELECT * FROM pois
        WHERE city LIKE ?
          AND area LIKE ?
          AND category = ?
          AND avg_price_per_person <= ?
          {avoid_clause}
        ORDER BY {pref_order} rating DESC
        LIMIT ?
    """
    params = [city_pat, area_pat, category, price_cap] + avoid_params + [limit]
    rows = conn.execute(sql, params).fetchall()

    # Fallback: relax area filter if too few results
    if len(rows) < 3 and area:
        sql_fallback = f"""
            SELECT * FROM pois
            WHERE city LIKE ?
              AND category = ?
              AND avg_price_per_person <= ?
              {avoid_clause}
            ORDER BY {pref_order} rating DESC
            LIMIT ?
        """
        params_fallback = [city_pat, category, price_cap] + avoid_params + [limit]
        rows = conn.execute(sql_fallback, params_fallback).fetchall()

    conn.close()
    return [dict(r) for r in rows]


class POISearchNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        intent = state["intent"]
        city          = intent.get("city", "")
        area          = intent.get("area", "")
        must_cats     = intent.get("must_include_categories", [])
        food_pref     = intent.get("food_pref", [])
        culture_pref  = intent.get("culture_pref", [])
        avoid         = intent.get("avoid", [])
        budget_pp     = intent.get("budget_per_person", 9999)

        candidates: dict[str, list] = {}
        for cat in must_cats:
            if cat == "餐饮":
                pref = food_pref
            elif cat in ("文化", "娱乐"):
                pref = culture_pref
            else:
                pref = []
            candidates[cat] = _query_category(city, area, cat, pref, avoid, budget_pp)

        updates = list(state.get("stream_updates", []))
        summary = "、".join(f"{cat}{len(v)}个" for cat, v in candidates.items())
        updates.append(f"找到候选POI：{summary}")

        return {**state, "candidates": candidates, "stream_updates": updates}
