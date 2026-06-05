import json
import math
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
import route_planner.i18n as i18n

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "poi.db")
_BIZ_SLOT = re.compile(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})")


def _is_open(business_hours: str, time_range: dict | None) -> bool:
    if not business_hours or not time_range:
        return True
    try:
        sh, sm = map(int, time_range["start"].split(":"))
        eh, em = map(int, time_range["end"].split(":"))
        intent_start = sh * 60 + sm
        intent_end   = eh * 60 + em
        for oh, om, ch, cm in ((int(a), int(b), int(c), int(d))
                                for a, b, c, d in _BIZ_SLOT.findall(business_hours)):
            if (oh * 60 + om) < intent_end and (ch * 60 + cm) > intent_start:
                return True
        return False
    except Exception:
        return True


def _amap_search(city: str, area: str, category: str, limit: int = 8) -> list[dict]:
    amap_key = os.environ.get("AMAP_API_KEY", "")
    if not amap_key:
        return []
    type_kw = {
        "餐饮": "餐厅|美食",
        "文化": "博物馆|景点|文化场馆|旅游景点",
        "娱乐": "娱乐休闲|主题公园",
    }.get(category, category)
    keywords = f"{area}{type_kw}" if area else type_kw
    params = urllib.parse.urlencode({
        "keywords": keywords,
        "city": city,
        "key": amap_key,
        "output": "json",
        "offset": str(limit),
        "extensions": "all",
    })
    try:
        with urllib.request.urlopen(
            f"https://restapi.amap.com/v3/place/text?{params}", timeout=5
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    if data.get("status") != "1" or not data.get("pois"):
        return []
    result = []
    for p in data["pois"][:limit]:
        loc = (p.get("location") or "0,0").split(",")
        biz = p.get("biz_ext") or {}
        try:
            lat, lng = float(loc[1]), float(loc[0])
        except (IndexError, ValueError):
            continue
        result.append({
            "id": f"amap_{p.get('id', '')}",
            "name": p.get("name", ""),
            "name_en": "",
            "category": category,
            "sub_category": (p.get("type") or "").split(";")[-1],
            "address": p.get("address", "") or "",
            "address_en": "",
            "city": city,
            "area": area,
            "lat": lat,
            "lng": lng,
            "rating": float(biz.get("rating") or 0),
            "taste_rating": 0.0, "decor_rating": 0.0, "service_rating": 0.0,
            "hygiene_rating": 0.0, "value_rating": 0.0,
            "review_count": 0, "half_year_sales": 0,
            "avg_price_per_person": float(biz.get("cost") or 0),
            "queue_risk": "低", "queue_minutes_peak": 0, "queue_minutes_offpeak": 0,
            "has_group_buy": 0, "group_buy_title": "",
            "group_buy_original_price": 0.0, "group_buy_current_price": 0.0,
            "business_hours": p.get("opentime_week") or "",
            "trend_tag": "高德數據", "recommend_count": 0,
        })
    return result


def _query_category(
    city: str,
    area: str,
    category: str,
    pref_sub_categories: list,
    avoid_sub_categories: list,
    budget_pp: float,
    time_range: dict | None = None,
    visited_ids: set | None = None,
    limit: int = 10,
) -> tuple[list[dict], bool]:
    """Return (results, used_amap_fallback)."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row

    city_pat = f"%{city}%" if city else "%"
    area_pat = f"%{area}%" if area else "%"
    price_cap = budget_pp * 1.2

    pref_cases = " ".join(
        f"WHEN sub_category LIKE '%{p}%' THEN {i}" for i, p in enumerate(pref_sub_categories)
    )
    pref_order = f"CASE {pref_cases} ELSE {len(pref_sub_categories)} END," if pref_cases else ""

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

    if len(rows) < 3 and area:
        sql_fb = f"""
            SELECT * FROM pois
            WHERE city LIKE ?
              AND category = ?
              AND avg_price_per_person <= ?
              {avoid_clause}
            ORDER BY {pref_order} rating DESC
            LIMIT ?
        """
        params_fb = [city_pat, category, price_cap] + avoid_params + [limit]
        rows = conn.execute(sql_fb, params_fb).fetchall()

    conn.close()
    results = [dict(r) for r in rows]

    # Filter visited POIs
    if visited_ids:
        results = [r for r in results if r["id"] not in visited_ids]

    # Filter by business hours (soft: keep if too few remain)
    if time_range:
        open_results = [r for r in results if _is_open(r.get("business_hours", ""), time_range)]
        if len(open_results) >= 3:
            results = open_results

    # Amap fallback when DB is insufficient
    used_amap = False
    if len(results) < 3:
        amap_rows = _amap_search(city, area, category)
        if visited_ids:
            amap_rows = [r for r in amap_rows if r["id"] not in visited_ids]
        results = results + amap_rows
        used_amap = bool(amap_rows)

    return results, used_amap


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
        time_range    = intent.get("time_range")

        memory = state.get("user_memory", {})
        visited_ids = set(memory.get("visited_poi_ids", []))

        candidates: dict[str, list] = {}
        used_amap_cats: list[str] = []

        for cat in must_cats:
            pref = food_pref if cat == "餐饮" else (culture_pref if cat in ("文化", "娱乐") else [])
            rows, used_amap = _query_category(
                city, area, cat, pref, avoid, budget_pp,
                time_range=time_range, visited_ids=visited_ids,
            )
            candidates[cat] = rows
            if used_amap:
                used_amap_cats.append(cat)

        lang = state.get("language", "zh-TW")
        updates = list(state.get("stream_updates", []))

        if i18n.normalize(lang) == "en":
            summary = ", ".join(
                f"{len(v)} {i18n.translate_field('category', cat, lang)}" for cat, v in candidates.items()
            )
        elif i18n.normalize(lang) == "zh-CN":
            summary = "、".join(f"{cat}{len(v)}个" for cat, v in candidates.items())
        else:
            summary = "、".join(
                f"{i18n.translate_field('category', cat, lang)}{len(v)}個"
                for cat, v in candidates.items()
            )
        updates.append(i18n.step("poi_found", lang, summary=summary))

        if used_amap_cats:
            cats_display = ", ".join(
                i18n.translate_field("category", c, lang) for c in used_amap_cats
            )
            updates.append(i18n.step("amap_fallback", lang, cats=cats_display))

        return {**state, "candidates": candidates, "stream_updates": updates}
