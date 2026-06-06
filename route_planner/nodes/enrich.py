from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
import route_planner.i18n as i18n

# ---------------------------------------------------------------------------
# POI tag system
# ---------------------------------------------------------------------------

_INDOOR_SUB_CATS = {
    "博物館", "博物馆", "美術館", "美术馆", "藝術館", "艺术馆", "商場", "商场",
    "室內展覽", "室内展览", "圖書館", "图书馆", "電影院", "电影院",
    "茶餐廳", "咖啡店", "甜品", "麵包店", "餐廳", "餐厅", "火鍋", "火锅",
}

_OUTDOOR_SUB_CATS = {
    "公園", "公园", "郊野公園", "郊野公园", "泳灘", "海灘", "海滩",
    "城市地標", "城市地标", "觀景地標", "旅遊景點", "旅游景点",
}


def _compute_tags(poi: dict, scenarios: list, weather: dict) -> tuple[list[str], list[str]]:
    """Return (positive_tags, risk_tags) for a single POI."""
    tags: list[str] = []
    risk_tags: list[str] = []

    rating         = poi.get("rating") or 0
    review_count   = poi.get("review_count") or 0
    value_rating   = poi.get("value_rating") or 0
    has_gb         = bool(poi.get("has_group_buy"))
    orig_price     = poi.get("group_buy_original_price") or 0
    curr_price     = poi.get("group_buy_current_price") or 0
    half_sales     = poi.get("half_year_sales") or 0
    sub_cat        = poi.get("sub_category", "")
    scen_tags      = poi.get("scenario_tags", "") or ""

    rl   = poi.get("risk_signal_level", "")
    ql   = poi.get("queue_signal_level", "")
    ll   = poi.get("local_authenticity_level", "")
    pl   = poi.get("photo_hotness_level", "")
    rmr  = poi.get("risk_mention_rate")
    qmr  = poi.get("queue_mention_rate")
    lmr  = poi.get("local_mention_rate")
    pmr  = poi.get("photo_mention_rate")

    # Positive tags
    if rating >= 4.5 and review_count > 200:
        tags.append("高口碑")
    if has_gb and orig_price > 0 and curr_price > 0 and (orig_price - curr_price) / orig_price >= 0.2:
        tags.append("團購划算")
    if value_rating >= 4.5 or (rating >= 4.3 and poi.get("avg_price_per_person", 999) < 80):
        tags.append("性價比高")
    if ll == "High" or (lmr is not None and lmr >= 0.55):
        tags.append("本地人常去")
    if pl == "High" or (pmr is not None and pmr >= 0.35):
        tags.append("拍照出片")
    if ql == "Low" or (qmr is not None and qmr <= 0.12):
        tags.append("低排隊")
    if half_sales < 800 and rating >= 4.3 and review_count > 30:
        tags.append("冷門寶藏")
    if "情侶約會" in scen_tags or "情侣约会" in scen_tags:
        tags.append("適合情侶")
    if "家庭親子" in scen_tags or "家庭亲子" in scen_tags:
        tags.append("親子友好")

    # Weather-aware: indoor tag
    if weather.get("prefer_indoor"):
        is_indoor = (
            any(w in sub_cat for w in _INDOOR_SUB_CATS)
            or poi.get("category") in {"餐饮", "Dining", "餐飲"}
        )
        if is_indoor:
            tags.append("雨天友好")

    # Risk tags
    if rl == "High" or (rmr is not None and rmr >= 0.75):
        risk_tags.append("踩雷風險")
    if ql == "High" or (qmr is not None and qmr >= 0.45):
        risk_tags.append("排隊較高")
    if half_sales >= 5000 and (poi.get("year_max") or 0) >= 2024:
        risk_tags.append("網紅打卡")  # popular but might be crowded

    return tags, risk_tags


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
        scenarios = intent.get("scenarios", [])
        weather = state.get("weather", {})

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
            pos_tags_raw, risk_tags_raw = _compute_tags(poi, scenarios, weather)
            pos_tags  = i18n.translate_tags(pos_tags_raw,  lang)
            risk_tags = i18n.translate_tags(risk_tags_raw, lang)
            # Language-aware name selection:
            #   en     → name_en if available, else Chinese name
            #   zh-CN  → to_simplified(name)  (DB stores Traditional)
            #   zh-TW  → name as-is (already Traditional)
            name_en = poi.get("name_en") or ""
            lang_key_local = i18n.normalize(lang)
            if lang_key_local == "en":
                display_name = name_en if name_en else poi["name"]
            elif lang_key_local == "zh-CN":
                display_name = i18n.to_simplified(poi["name"])
            else:
                display_name = poi["name"]
            enriched.append({
                "poi_id": poi_id,
                "order": item.get("order", len(enriched) + 1),
                "name": display_name,
                "name_en": name_en,
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
                "pref_matched": matched,
                "risk_mention_rate": poi.get("risk_mention_rate"),
                "queue_mention_rate": poi.get("queue_mention_rate"),
                "photo_mention_rate": poi.get("photo_mention_rate"),
                "local_mention_rate": poi.get("local_mention_rate"),
                "accessibility_mention_rate": poi.get("accessibility_mention_rate"),
                "year_max": poi.get("year_max"),
                "risk_signal_level": poi.get("risk_signal_level", ""),
                "queue_signal_level": poi.get("queue_signal_level", ""),
                "local_authenticity_level": poi.get("local_authenticity_level", ""),
                "photo_hotness_level": poi.get("photo_hotness_level", ""),
                "scenario_tags": poi.get("scenario_tags", ""),
                "tags": pos_tags,
                "risk_tags": risk_tags,
            })

        enriched.sort(key=lambda x: x["order"])

        updates = list(state.get("stream_updates", []))
        updates.append(i18n.step("enrich_done", lang))

        return {**state, "route": enriched, "stream_updates": updates}
