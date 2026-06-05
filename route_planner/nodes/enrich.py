from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
import route_planner.i18n as i18n


def _group_buy(poi: dict) -> dict | None:
    if not poi.get("has_group_buy"):
        return None
    orig = poi.get("group_buy_original_price", 0)
    curr = poi.get("group_buy_current_price", 0)
    discount = f"{curr / orig * 10:.1f}折" if orig > 0 else ""
    return {
        "title": poi.get("group_buy_title", ""),
        "original_price": orig,
        "current_price": curr,
        "discount": discount,
    }


def _trend_tag(poi: dict) -> str:
    tag = poi.get("trend_tag", "")
    sales = poi.get("half_year_sales", 0)
    if sales >= 10000:
        return f"{tag}（已售{sales / 10000:.1f}万单）"
    if sales > 0:
        return f"{tag}（已售{sales}单）"
    return tag


_DINING_CATS = {"餐饮", "Dining", "餐飲"}
_CULTURE_CATS = {"文化", "娱乐", "自然", "Culture", "Entertainment", "Nature"}


def _check_pref_match(poi: dict, food_pref: list, culture_pref: list) -> bool:
    """Return True if this POI's sub_category matches user's relevant preference."""
    sub = poi.get("sub_category", "")
    cat = poi.get("category", "")
    if cat in _DINING_CATS and food_pref:
        return any(p in sub for p in food_pref)
    if cat in _CULTURE_CATS and culture_pref:
        return any(p in sub for p in culture_pref)
    return True


class EnrichNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        candidates = state["candidates"]
        selection = state["route"]
        intent = state.get("intent", {})
        lang = state.get("language", "zh-TW")
        food_pref = intent.get("food_pref", [])
        culture_pref = intent.get("culture_pref", [])

        poi_lookup: dict[str, dict] = {
            poi["id"]: poi
            for pois in candidates.values()
            for poi in pois
        }

        enriched = []
        for item in selection:
            poi_id = item.get("poi_id") or item.get("id", "")
            poi = poi_lookup.get(poi_id)
            if not poi:
                if item.get("name"):
                    enriched.append(item)
                continue
            matched = _check_pref_match(poi, food_pref, culture_pref)
            tf = lambda field, val: i18n.translate_field(field, val, lang)
            enriched.append({
                "poi_id": poi_id,
                "order": item.get("order", len(enriched) + 1),
                "name": poi["name"],
                "name_en": poi.get("name_en", ""),
                "category": tf("category", poi["category"]),
                "sub_category": tf("sub_category", poi.get("sub_category", "")),
                "address": poi["address"],
                "address_en": poi.get("address_en", ""),
                "city": poi.get("city", ""),
                "area": poi.get("area", ""),
                "lat": poi["lat"],
                "lng": poi["lng"],
                "rating": poi["rating"],
                "taste_rating": poi.get("taste_rating", 0),
                "decor_rating": poi.get("decor_rating", 0),
                "service_rating": poi.get("service_rating", 0),
                "hygiene_rating": poi.get("hygiene_rating", 0),
                "half_year_sales": poi.get("half_year_sales", 0),
                "recommend_count": poi.get("recommend_count", 0),
                "avg_price_per_person": poi.get("avg_price_per_person", 0),
                "queue_risk": tf("queue_risk", poi.get("queue_risk", "低")),
                "queue_risk_tip": i18n.queue_tip(poi, lang),
                "has_group_buy": poi.get("has_group_buy", False),
                "group_buy": _group_buy(poi),
                "stay_minutes": item.get("stay_minutes", 60),
                "trend_tag": tf("trend_tag", _trend_tag(poi)),
                "business_hours": poi.get("business_hours", ""),
                "pref_matched": matched,  # True = matches user preference; False = best available substitute
            })

        enriched.sort(key=lambda x: x["order"])

        updates = list(state.get("stream_updates", []))
        updates.append(i18n.step("enrich_done", lang))

        return {**state, "route": enriched, "stream_updates": updates}
