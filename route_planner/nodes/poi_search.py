import json
import os
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mock_poi.json")

with open(_DATA_PATH, encoding="utf-8") as _f:
    _POI_DB: list[dict] = json.load(_f)


def _area_match(poi: dict, area: str, city: str) -> bool:
    poi_city = poi.get("city", "")
    poi_area = poi.get("area", "")
    city_ok = not city or city in poi_city or poi_city in city
    area_ok = not area or area in poi_area or poi_area in area
    return city_ok and area_ok


class POISearchNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        intent = state["intent"]
        city = intent.get("city", "")
        area = intent.get("area", "")
        must_cats = intent.get("must_include_categories", [])
        food_pref = intent.get("food_pref", [])
        budget_pp = intent.get("budget_per_person", 9999)

        nearby = [p for p in _POI_DB if _area_match(p, area, city)]
        if len(nearby) < 5:
            nearby = [p for p in _POI_DB if city in p.get("city", "")]

        candidates: dict[str, list] = {}
        for cat in must_cats:
            pool = [p for p in nearby if p["category"] == cat]

            if cat == "餐饮" and food_pref:
                preferred = [p for p in pool if any(fp in p.get("sub_category", "") for fp in food_pref)]
                rest = [p for p in pool if p not in preferred]
                pool = preferred + rest

            pool = [p for p in pool if p.get("avg_price_per_person", 0) <= budget_pp * 1.2]
            pool.sort(key=lambda x: x.get("rating", 0), reverse=True)
            candidates[cat] = pool[:10]

        updates = list(state.get("stream_updates", []))
        summary = "、".join(f"{cat}{len(v)}个" for cat, v in candidates.items())
        updates.append(f"找到候选POI：{summary}")

        return {**state, "candidates": candidates, "stream_updates": updates}
