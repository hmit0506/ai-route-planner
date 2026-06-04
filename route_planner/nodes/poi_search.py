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
    food_pref: list,
    budget_pp: float,
    limit: int = 10,
) -> list[dict]:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row

    city_pat = f"%{city}%" if city else "%"
    area_pat = f"%{area}%" if area else "%"
    price_cap = budget_pp * 1.2

    rows = conn.execute(
        """
        SELECT * FROM pois
        WHERE city LIKE ?
          AND area LIKE ?
          AND category = ?
          AND avg_price_per_person <= ?
        ORDER BY
            CASE WHEN ? != '' AND sub_category LIKE ? THEN 0 ELSE 1 END,
            rating DESC
        LIMIT ?
        """,
        (city_pat, area_pat, category, price_cap,
         food_pref[0] if food_pref else "", f"%{food_pref[0]}%" if food_pref else "%",
         limit),
    ).fetchall()

    # Fallback: city only if area returns too few results
    if len(rows) < 3 and area:
        rows = conn.execute(
            """
            SELECT * FROM pois
            WHERE city LIKE ?
              AND category = ?
              AND avg_price_per_person <= ?
            ORDER BY rating DESC
            LIMIT ?
            """,
            (city_pat, category, price_cap, limit),
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


class POISearchNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        intent = state["intent"]
        city       = intent.get("city", "")
        area       = intent.get("area", "")
        must_cats  = intent.get("must_include_categories", [])
        food_pref  = intent.get("food_pref", [])
        budget_pp  = intent.get("budget_per_person", 9999)

        candidates: dict[str, list] = {}
        for cat in must_cats:
            pref = food_pref if cat == "餐饮" else []
            candidates[cat] = _query_category(city, area, cat, pref, budget_pp)

        updates = list(state.get("stream_updates", []))
        summary = "、".join(f"{cat}{len(v)}个" for cat, v in candidates.items())
        updates.append(f"找到候选POI：{summary}")

        return {**state, "candidates": candidates, "stream_updates": updates}
