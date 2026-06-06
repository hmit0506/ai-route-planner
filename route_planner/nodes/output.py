import math
import os
import urllib.request
import urllib.parse
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.llm import call_llm
import route_planner.i18n as i18n


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _transport_text(km: float, lang: str = "zh-TW") -> str:
    return i18n.transport_text(km, lang)


def _fetch_walking_polyline(
    origin_lng: float, origin_lat: float,
    dest_lng: float, dest_lat: float,
    api_key: str,
) -> str | None:
    """Call Amap walking directions API; return semicolon-separated 'lng,lat' polyline or None."""
    params = urllib.parse.urlencode({
        "origin": f"{origin_lng},{origin_lat}",
        "destination": f"{dest_lng},{dest_lat}",
        "key": api_key,
    })
    url = f"https://restapi.amap.com/v3/direction/walking?{params}"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
        if data.get("status") != "1":
            return None
        steps = data["route"]["paths"][0]["steps"]
        # Each step has a polyline string "lng,lat;lng,lat;..."
        full = ";".join(s["polyline"] for s in steps)
        return _downsample(full, max_points=40)
    except Exception:
        return None


def _downsample(polyline: str, max_points: int) -> str:
    """Reduce number of points to keep static map URL under length limit."""
    pts = polyline.split(";")
    if len(pts) <= max_points:
        return polyline
    step = len(pts) / max_points
    kept = [pts[round(i * step)] for i in range(max_points)]
    # Always include last point
    if kept[-1] != pts[-1]:
        kept[-1] = pts[-1]
    return ";".join(kept)


def _build_map_url(route: list, polylines: list[str | None]) -> str:
    if not route:
        return ""
    api_key = os.getenv("AMAP_API_KEY", "YOUR_AMAP_KEY")
    labels = "ABCDEFGHIJ"
    markers = "|".join(
        f"mid,,{labels[i] if i < len(labels) else i+1}:{p['lng']},{p['lat']}"
        for i, p in enumerate(route)
    )
    avg_lat = sum(p["lat"] for p in route) / len(route)
    avg_lng = sum(p["lng"] for p in route) / len(route)

    base = (
        f"https://restapi.amap.com/v3/staticmap"
        f"?location={avg_lng:.4f},{avg_lat:.4f}"
        f"&zoom=14&size=750*400"
        f"&markers={markers}"
        f"&key={api_key}"
    )

    # Append each walking path segment
    path_parts = []
    for polyline in polylines:
        if polyline:
            path_parts.append(f"4,0x0065FF,0.7,,:{polyline}")
    if path_parts:
        base += "&paths=" + "|".join(path_parts)

    return base


def _nav_url(poi: dict) -> str:
    name = urllib.parse.quote(poi.get("name", ""))
    return (
        f"https://uri.amap.com/navigation"
        f"?to={poi['lng']},{poi['lat']},{name}"
        f"&mode=walk&coordinate=gaode&callnative=1"
    )


def _build_fulfillment(route: list, intent: dict, lang: str = "zh-TW") -> dict:
    """Compare actual route against user intent, report satisfied/unmatched/tips."""
    # Translate pref labels to output language so messages don't mix languages
    def _xlat(val: str) -> str:
        return i18n.translate_field("sub_category", val, lang)

    # Localize POI names: zh-CN converts Traditional→Simplified proper nouns
    def _name(poi: dict) -> str:
        n = poi.get("name", "")
        if i18n.normalize(lang) == "zh-CN":
            return i18n.to_simplified(n)
        return n

    food_pref      = [_xlat(p) for p in intent.get("food_pref", [])]
    culture_pref   = [_xlat(p) for p in intent.get("culture_pref", [])]
    dining_count_r = intent.get("dining_count", 0)
    avoid          = [_xlat(a) for a in intent.get("avoid", [])]
    sep = " / " if i18n.normalize(lang) == "en" else "、"

    satisfied, unmatched, tips = [], [], []

    # Check dining_count
    dining_pois = [p for p in route if p.get("category") in _DINING_CATS]
    if dining_count_r > 0:
        if len(dining_pois) == dining_count_r:
            satisfied.append(i18n.f("dining_ok", lang, n=dining_count_r))
        elif len(dining_pois) < dining_count_r:
            unmatched.append(i18n.f("dining_mismatch", lang, req=dining_count_r, got=len(dining_pois)))
            tips.append(i18n.f("dining_tip", lang))
        else:
            # Too many dining stops
            unmatched.append(i18n.f("dining_excess", lang, req=dining_count_r, got=len(dining_pois)))
            tips.append(i18n.f("dining_excess_tip", lang))

    # Check food_pref matching
    if food_pref:
        matched_dining   = [p for p in dining_pois if p.get("pref_matched")]
        unmatched_dining = [p for p in dining_pois if not p.get("pref_matched")]
        if matched_dining:
            satisfied.append(i18n.f("food_ok", lang,
                pref=sep.join(food_pref), names=sep.join(_name(p) for p in matched_dining)))
        if unmatched_dining:
            names = sep.join(_name(p) for p in unmatched_dining)
            subs  = sep.join(p.get("sub_category", "") for p in unmatched_dining)
            unmatched.append(i18n.f("food_miss", lang, pref=sep.join(food_pref), sub=subs, names=names))
            tips.append(i18n.f("food_tip", lang, cuisine=sep.join(food_pref)))

    # Check culture_pref matching
    if culture_pref:
        cultural_pois    = [p for p in route if p.get("category") in ("文化", "娱乐", "自然", "Culture", "Entertainment", "Nature")]
        matched_cultural   = [p for p in cultural_pois if p.get("pref_matched")]
        unmatched_cultural = [p for p in cultural_pois if not p.get("pref_matched")]
        if matched_cultural:
            satisfied.append(i18n.f("culture_ok", lang,
                pref=sep.join(culture_pref), names=sep.join(_name(p) for p in matched_cultural)))
        if unmatched_cultural:
            names = sep.join(_name(p) for p in unmatched_cultural)
            subs  = sep.join(p.get("sub_category", "") for p in unmatched_cultural)
            unmatched.append(i18n.f("culture_miss", lang, pref=sep.join(culture_pref), sub=subs, names=names))
            tips.append(i18n.f("culture_tip", lang, pref=sep.join(culture_pref)))

    # Check avoid violations
    if avoid:
        violated = [p for p in route if any(a in p.get("sub_category", "") for a in avoid)]
        if violated:
            names = sep.join(_name(p) for p in violated)
            unmatched.append(i18n.f("avoid_violated", lang, avoid=sep.join(avoid), names=names))
            tips.append(i18n.f("avoid_tip", lang, names=names))

    return {"satisfied": satisfied, "unmatched": unmatched, "tips": tips}


_DINING_CATS = {"餐饮", "Dining", "餐飲"}

_XHS_TEMPLATE = {
    "zh-TW": (
        "📍{city}{area}路線｜{party}人｜預算{budget}元\n"
        "🗺 {route_stops}\n"
        "⏱ 全程約{total_hours}小時\n"
        "💰 總消費約{total_cost}元，人均{per_person}元\n"
        "{scenarios_line}"
        "{weather_line}"
        "{deal_line}"
        "{risk_line}"
        "{hashtags}"
    ),
    "zh-CN": (
        "📍{city}{area}路线｜{party}人｜预算{budget}元\n"
        "🗺 {route_stops}\n"
        "⏱ 全程约{total_hours}小时\n"
        "💰 总消费约{total_cost}元，人均{per_person}元\n"
        "{scenarios_line}"
        "{weather_line}"
        "{deal_line}"
        "{risk_line}"
        "{hashtags}"
    ),
    "en": (
        "📍 {city} {area} Route | {party} pax | Budget HKD {budget}\n"
        "🗺 {route_stops}\n"
        "⏱ Approx {total_hours}h total\n"
        "💰 Est. HKD {total_cost} total, HKD {per_person}/person\n"
        "{scenarios_line}"
        "{weather_line}"
        "{deal_line}"
        "{risk_line}"
        "{hashtags}"
    ),
}

_SCENARIOS_DISPLAY = {
    "zh-TW": {
        "情侶約會": "情侶約會", "情侣约会": "情侶約會",
        "朋友聚餐": "朋友聚餐", "家庭親子": "親子遊", "家庭亲子": "親子遊",
        "慶生": "慶生", "庆生": "慶生", "一人食": "一人食",
        "打卡拍照": "拍照打卡", "商務接待": "商務接待", "商务接待": "商務接待",
    },
    "zh-CN": {
        "情侶約會": "情侣约会", "情侣约会": "情侣约会",
        "朋友聚餐": "朋友聚餐", "家庭親子": "亲子游", "家庭亲子": "亲子游",
        "慶生": "庆生", "庆生": "庆生", "一人食": "一人食",
        "打卡拍照": "拍照打卡", "商務接待": "商务接待", "商务接待": "商务接待",
    },
    "en": {
        "情侶約會": "Couples", "情侣约会": "Couples",
        "朋友聚餐": "Friends", "家庭親子": "Families", "家庭亲子": "Families",
        "慶生": "Birthdays", "庆生": "Birthdays", "一人食": "Solo",
        "打卡拍照": "Photo Lovers", "商務接待": "Business", "商务接待": "Business",
    },
}


def _build_xiaohongshu(route: list, intent: dict, weather: dict, lang: str = "zh-TW") -> str:
    lang_key = i18n.normalize(lang)
    tpl      = _XHS_TEMPLATE.get(lang_key, _XHS_TEMPLATE["zh-TW"])
    sc_map   = _SCENARIOS_DISPLAY.get(lang_key, _SCENARIOS_DISPLAY["zh-TW"])

    city      = intent.get("city", "")
    area      = intent.get("area", "")
    budget    = intent.get("budget_total", 0)
    party     = intent.get("party_size", 2)
    scenarios = intent.get("scenarios", [])

    if lang_key == "en":
        city = i18n.translate_field("city", city, lang)
        area = i18n.translate_field("area", area, lang)
    elif lang_key == "zh-CN":
        city = i18n.to_simplified(city)
        area = i18n.to_simplified(area)
    else:  # zh-TW: ensure Traditional even if intent came in Simplified
        city = i18n.to_traditional(city)
        area = i18n.to_traditional(area)

    names = [p.get("name", "") for p in route]
    if lang_key == "zh-CN":
        names = [i18n.to_simplified(n) for n in names]
    route_stops = " → ".join(names)

    total_mins  = sum(p.get("stay_minutes", 60) for p in route)
    total_hours = round(total_mins / 60, 1)
    dining_cost = sum(
        (p.get("group_buy") or {}).get("current_price", 0) or p.get("avg_price_per_person", 0)
        for p in route if p.get("category") in _DINING_CATS
    )
    total_cost = int(dining_cost)
    per_person = int(dining_cost / max(party, 1))

    if scenarios:
        sc_labels = [sc_map.get(s, s) for s in scenarios]
        sep = " | " if lang_key == "en" else "｜"
        if lang_key == "en":
            scenarios_line = f"👥 Great for: {sep.join(sc_labels)}\n"
        else:
            tag = "適合" if lang_key == "zh-TW" else "适合"
            scenarios_line = f"👥 {tag}：{sep.join(sc_labels)}\n"
    else:
        scenarios_line = ""

    if weather:
        wd   = weather.get("weather", "")
        temp = int(weather.get("temperature", 0))
        cond = weather.get("condition", "")
        if lang_key == "en":
            weather_line = f"🌤 Weather: {wd} {temp}°C"
            if cond in ("rain", "storm"):
                weather_line += " — Indoor route, bring an umbrella ☂️"
            weather_line += "\n"
        else:
            label = "天氣" if lang_key == "zh-TW" else "天气"
            weather_line = f"🌤 {label}：{wd} {temp}°C"
            if cond in ("rain", "storm"):
                tip = "，已安排室內路線，記得帶傘☂️" if lang_key == "zh-TW" else "，已安排室内路线，记得带伞☂️"
                weather_line += tip
            weather_line += "\n"
    else:
        weather_line = ""

    deal_pois = [p for p in route if p.get("has_group_buy") and p.get("group_buy")]
    if deal_pois:
        p0 = deal_pois[0]
        gb = p0["group_buy"]
        nm = p0.get("name", "")
        if lang_key == "zh-CN":
            nm = i18n.to_simplified(nm)
        if lang_key == "en":
            deal_line = f"🎟 Deal: {nm} — HKD {gb.get('current_price',0)} (was {gb.get('original_price',0)})\n"
        else:
            unit = "元"
            deal_line = f"🎟 團購：{nm} {gb.get('current_price',0)}{unit}（原{gb.get('original_price',0)}{unit}）\n" if lang_key == "zh-TW" else f"🎟 团购：{nm} {gb.get('current_price',0)}{unit}（原{gb.get('original_price',0)}{unit}）\n"
    else:
        deal_line = ""

    high_q = [p for p in route if p.get("queue_risk") in ("高", "High") or "排隊較高" in (p.get("risk_tags") or [])]
    if high_q:
        if lang_key == "en":
            risk_line = f"⚠️ Heads up: {', '.join(p.get('name','') for p in high_q)} can get busy\n"
        else:
            hq = "、".join(p.get("name", "") for p in high_q)
            if lang_key == "zh-CN":
                hq = i18n.to_simplified(hq)
            tip = "高峰期排隊較多，建議提早到" if lang_key == "zh-TW" else "高峰期排队较多，建议提早到"
            risk_line = f"⚠️ 避坑：{hq} {tip}\n"
    else:
        risk_line = ""

    ht_city = i18n.to_traditional(intent.get("city","")) if lang_key == "zh-TW" else (i18n.to_simplified(intent.get("city","")) if lang_key == "zh-CN" else city.replace(" ", ""))
    ht_area = i18n.to_traditional(intent.get("area","")) if lang_key == "zh-TW" else (i18n.to_simplified(intent.get("area","")) if lang_key == "zh-CN" else area.replace(" ", ""))
    if lang_key == "en":
        hashtags = f"#{ht_city.replace(' ','')} #{ht_area.replace(' ','')} #TravelGuide #LocalLife #FoodTrail"
    else:
        hashtags = f"#{ht_city}{ht_area} #路線推薦 #本地生活 #美食打卡" if lang_key == "zh-TW" else f"#{ht_city}{ht_area} #路线推荐 #本地生活 #美食打卡"

    return tpl.format(
        city=city, area=area, party=party, budget=budget,
        route_stops=route_stops, total_hours=total_hours,
        total_cost=total_cost, per_person=per_person,
        scenarios_line=scenarios_line, weather_line=weather_line,
        deal_line=deal_line, risk_line=risk_line, hashtags=hashtags,
    ).strip()


def _llm_xiaohongshu(route: list, intent: dict, weather: dict, lang: str) -> str:
    """LLM-generated xiaohongshu post. Falls back to template on any failure."""
    lang_key = i18n.normalize(lang)

    poi_lines = []
    for p in route:
        name    = p.get("name", "")
        cat     = p.get("category", "")
        rating  = p.get("rating", 0)
        gb      = p.get("group_buy")
        tags    = p.get("tags") or []
        rtags   = p.get("risk_tags") or []
        gb_str  = f"，团购{gb['current_price']}元" if gb else ""
        tag_str = f"，{'/'.join(tags[:3])}" if tags else ""
        rt_str  = f"，⚠{'/'.join(rtags[:2])}" if rtags else ""
        poi_lines.append(f"- {name}（{cat}，{rating}分{gb_str}{tag_str}{rt_str}）")

    city      = intent.get("city", "")
    area      = intent.get("area", "")
    budget    = intent.get("budget_total", 0)
    party     = intent.get("party_size", 2)
    scenarios = intent.get("scenarios", [])
    food_pref = intent.get("food_pref", [])

    weather_line = ""
    if weather and weather.get("condition", "clear") != "clear":
        weather_line = f"\n天气：{weather.get('weather','')} {int(weather.get('temperature',0))}°C"

    total_mins  = sum(p.get("stay_minutes", 60) for p in route)
    dining_cost = sum(
        (p.get("group_buy") or {}).get("current_price", 0) or p.get("avg_price_per_person", 0)
        for p in route if p.get("category") in _DINING_CATS
    )

    if lang_key == "en":
        lang_inst = (
            "THIS POST MUST BE WRITTEN IN ENGLISH ONLY. "
            "Every single word, sentence, and hashtag must be in English. "
            "Do NOT use any Chinese characters (Traditional or Simplified). "
            "First-person voice, casual and enthusiastic like a real social media creator sharing a personal experience. "
            "Prices in HKD."
        )
        struct_hint = (
            "Exact structure to follow:\n"
            "① One-line catchy title with emoji and a number (e.g. '5 hrs, HKD 400 — the perfect couple date in Mong Kok! 💑')\n"
            "② Opening hook: 1-2 sentences that draw the reader in, pose a question or share a relatable feeling\n"
            "③ Each stop gets its own numbered entry (1️⃣ 2️⃣ 3️⃣ …) with: bold name, star rating, "
            "a specific dish or moment you loved, why it's good for the occasion, and any deal/tip\n"
            "④ '💡 Tips' section: budget breakdown, best arrival time, practical warnings (⚠️ for queues or cash-only)\n"
            "⑤ 5–8 English hashtags on the last line\n"
            "Use at least one emoji per paragraph. Be specific — name actual dishes, prices, feelings."
        )
    elif lang_key == "zh-CN":
        lang_inst = "【强制要求】全程使用简体中文，严禁出现任何繁体字（如：來→来，為→为，時→时，館→馆，這→这）。语气活泼自然，像朋友分享，不像广告。话题标签用简体。"
        struct_hint = "结构：① 吸睛标题（含数字或感叹词）→ ② 路线亮点（重点站点，带真实感受）→ ③ 实用Tips（预算/时间/注意事项）→ ④ 话题标签（5–8个，紧贴内容）"
    else:
        lang_inst = "【強制要求】全程繁體中文，嚴禁簡體字（如：来→來，为→為，时→時，馆→館，这→這）。語氣活潑自然，像朋友分享，不像廣告。話題標籤用繁體。"
        struct_hint = "結構：① 吸睛標題（含數字或感嘆詞）→ ② 路線亮點（重點站點，帶真實感受）→ ③ 實用Tips（預算/時間/注意事項）→ ④ 話題標籤（5–8個，緊貼內容）"

    system_prompt = f"""\
You are a lifestyle content creator writing a local travel guide post.

CRITICAL LANGUAGE RULE: {lang_inst}

Post requirements:
- {struct_hint}
- 200–350 words, emoji in every paragraph, title must have emoji
- Specific numbers, avoid generic phrases
- Output the post body only — no preamble like "Here is" or "以下是"
"""

    if lang_key == "en":
        user_msg = (
            f"City: {city} {area}\n"
            f"Party: {party} pax | Budget: HKD {budget} | Duration: ~{round(total_mins/60,1)}h\n"
            f"Occasion: {'/'.join(scenarios) if scenarios else 'Leisure outing'}\n"
            f"Food preference: {'/'.join(food_pref) if food_pref else 'No specific requirement'}\n"
            f"Estimated dining spend: HKD {int(dining_cost)}"
            f"{weather_line}\n\n"
            f"Route stops:\n" + "\n".join(poi_lines) + "\n\n"
            f"Write the English post now."
        )
    elif lang_key == "zh-CN":
        user_msg = (
            f"城市：{city} {area}\n"
            f"人数：{party}人｜预算：{budget}元｜行程约 {round(total_mins/60,1)} 小时\n"
            f"场合：{'/'.join(scenarios) if scenarios else '休闲出行'}\n"
            f"饮食偏好：{'/'.join(food_pref) if food_pref else '无特定要求'}\n"
            f"餐饮消费估算：{int(dining_cost)}元"
            f"{weather_line}\n\n"
            f"路线站点：\n" + "\n".join(poi_lines) + "\n\n"
            f"请用简体中文写一篇小红书攻略贴文。"
        )
    else:
        user_msg = (
            f"城市：{city} {area}\n"
            f"人數：{party}人｜預算：{budget}元｜行程約 {round(total_mins/60,1)} 小時\n"
            f"場合：{'/'.join(scenarios) if scenarios else '休閒出行'}\n"
            f"飲食偏好：{'/'.join(food_pref) if food_pref else '無特定要求'}\n"
            f"餐飲消費估算：{int(dining_cost)}元"
            f"{weather_line}\n\n"
            f"路線站點：\n" + "\n".join(poi_lines) + "\n\n"
            f"請用繁體中文寫一篇小紅書攻略貼文。"
        )

    def _cjk_ratio(text: str) -> float:
        cjk = sum(1 for c in text if "一" <= c <= "鿿")
        return cjk / max(len(text), 1)

    try:
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_msg}]
        text = call_llm(messages, parse_json=False).strip()

        # zh-CN: OpenCC is authoritative — always convert regardless of LLM behaviour
        if lang_key == "zh-CN":
            text = i18n.to_simplified(text)

        # en: if LLM output is mostly CJK, force a strict retry then fall back to template
        elif lang_key == "en" and _cjk_ratio(text) > 0.15:
            retry_msg = {
                "role": "user",
                "content": (
                    "Your previous response was in Chinese. "
                    "You MUST rewrite it entirely in English. "
                    "No Chinese characters allowed. English only."
                ),
            }
            text2 = call_llm(
                messages + [{"role": "assistant", "content": text}, retry_msg],
                parse_json=False,
            ).strip()
            text = text2 if _cjk_ratio(text2) < 0.1 else _build_xiaohongshu(route, intent, weather, lang)

        return text
    except Exception:
        return _build_xiaohongshu(route, intent, weather, lang)


def _build_summary(route: list, lang: str = "zh-TW") -> str:
    total_mins = sum(r.get("stay_minutes", 60) for r in route)
    gb_count = sum(1 for r in route if r.get("has_group_buy"))
    budget_used = sum(
        (r.get("group_buy") or {}).get("current_price", 0) or r.get("avg_price_per_person", 0)
        for r in route
        if r.get("category") in _DINING_CATS
    )
    return i18n.summary(len(route), total_mins, int(budget_used), gb_count, lang)


class OutputNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        route = [dict(r) for r in state["route"]]
        lang = state.get("language", "zh-TW")
        api_key = os.getenv("AMAP_API_KEY", "")

        # Fetch walking polylines + LLM xiaohongshu post in parallel
        n_segments = len(route) - 1
        intent_snap  = state.get("intent", {})
        weather_snap = state.get("weather", {})

        def _fetch_segment(i):
            poi, nxt = route[i], route[i + 1]
            return _fetch_walking_polyline(
                poi["lng"], poi["lat"], nxt["lng"], nxt["lat"], api_key
            ) if api_key else None

        def _gen_xhs(_):
            return _llm_xiaohongshu(route, intent_snap, weather_snap, lang)

        with ThreadPoolExecutor(max_workers=min(n_segments + 1, 6)) as ex:
            segment_futures = [ex.submit(_fetch_segment, i) for i in range(n_segments)]
            xhs_future      = ex.submit(_gen_xhs, None)
            polylines = [f.result() for f in segment_futures]
            xhs_post  = xhs_future.result()

        for i, poi in enumerate(route):
            poi["order"] = i + 1
            poi["navigation_url"] = _nav_url(poi)
            if i < len(route) - 1:
                nxt = route[i + 1]
                km = _haversine_km(poi["lat"], poi["lng"], nxt["lat"], nxt["lng"])
                poi["transport_to_next"] = _transport_text(km, lang)
                poi["transport_polyline"] = polylines[i]
            else:
                poi["transport_to_next"] = ""
                poi["transport_polyline"] = None

        map_url     = _build_map_url(route, polylines)
        fulfillment = _build_fulfillment(route, intent_snap, lang)
        summary     = _build_summary(route, lang)
        if fulfillment.get("unmatched"):
            summary += "（" + "；".join(fulfillment["unmatched"]) + "）"

        updates = list(state.get("stream_updates", []))
        updates.append(i18n.step("output_done", lang))

        # Surface fulfillment issues as visible step events
        for msg in fulfillment.get("unmatched", []):
            updates.append(f"⚠️ {msg}")
        for tip in fulfillment.get("tips", []):
            updates.append(f"💡 {tip}")

        return {
            **state,
            "route": route,
            "map_url": map_url,
            "summary": summary,
            "fulfillment_notes": fulfillment,
            "xiaohongshu_post": xhs_post,
            "stream_updates": updates,
        }
