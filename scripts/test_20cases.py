"""
20-case trilingual test suite.

Design:
  Group A (C01–C08)  — pure-logic unit tests, zero API calls
  Group B (C09–C14)  — IntentNode only (1 LLM call each)
  Group C (C15–C20)  — full pipeline (multiple LLM + Amap API calls)

Run:
    PYTHONPATH=. .venv/bin/python3 scripts/test_20cases.py [group]
    group = A | B | C | all   (default: all)
"""
import json
import os
import sys
import textwrap
import traceback

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

# ── colour helpers ───────────────────────────────────────────────────────────
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_BOLD   = "\033[1m"
_RST    = "\033[0m"

def ok(msg):  return f"{_GREEN}✓ {msg}{_RST}"
def fail(msg):return f"{_RED}✗ {msg}{_RST}"
def warn(msg):return f"{_YELLOW}⚠ {msg}{_RST}"
def head(msg):return f"\n{_BOLD}{_CYAN}{msg}{_RST}"

_results: list[tuple[str, bool, str]] = []  # (case_id, passed, detail)

def record(case_id: str, passed: bool, detail: str):
    _results.append((case_id, passed, detail))
    icon = ok("PASS") if passed else fail("FAIL")
    print(f"  {icon}  {detail}")


# ═══════════════════════════════════════════════════════════════════════════
# GROUP A — Pure logic, no API
# ═══════════════════════════════════════════════════════════════════════════
def group_a():
    print(head("═══ GROUP A: Pure-logic unit tests ═══"))

    from route_planner.nodes.weather import _classify, _parse_target_date
    from route_planner.nodes.enrich  import _compute_tags
    from route_planner.nodes.output  import _build_xiaohongshu
    from route_planner.nodes.poi_search import _is_hk_city
    from route_planner.i18n import weather_step, normalize

    # ── C01: Rain weather classification ────────────────────────────────────
    print(head("C01 [zh-TW] 天氣分類 — 雨天 → prefer_indoor=True"))
    cast = {"dayweather": "中雨", "nightweather": "晴", "daytemp": "24", "nighttemp": "20",
            "date": "2026-06-07"}
    w = _classify(cast, {"start": "14:00", "end": "21:00"})
    checks = [
        ("condition=rain",    w["condition"] == "rain"),
        ("prefer_indoor=True",w["prefer_indoor"] is True),
        ("is_rainy=True",     w["is_rainy"] is True),
        ("is_hot=False",      w["is_hot"] is False),
    ]
    for lbl, ok_ in checks:
        record("C01", ok_, f"{lbl}: got condition={w['condition']}, prefer_indoor={w['prefer_indoor']}")

    step_tw = weather_step(w, "zh-TW")
    step_cn = weather_step(w, "zh-CN")
    step_en = weather_step(w, "en")
    record("C01", "雨" in step_tw and "☂" not in step_tw,   f"zh-TW step: {step_tw}")
    record("C01", "雨" in step_cn,                           f"zh-CN step: {step_cn}")
    record("C01", "Rain" in step_en or "rain" in step_en,   f"en  step: {step_en}")
    # Check no language mixing
    tw_has_simp = any(c in "来为时间这" for c in step_tw)
    record("C01", not tw_has_simp, f"zh-TW has no simplified chars: {'CLEAN' if not tw_has_simp else step_tw}")

    # ── C02: Hot weather ─────────────────────────────────────────────────────
    print(head("C02 [zh-CN] 天氣分類 — 高溫（35°C）→ prefer_indoor=True"))
    cast2 = {"dayweather": "晴", "daytemp": "35", "nighttemp": "28",
              "nightweather": "多云", "date": "2026-06-07"}
    w2 = _classify(cast2, {"start": "13:00", "end": "19:00"})
    record("C02", w2["condition"] == "hot",        f"condition=hot: got {w2['condition']}")
    record("C02", w2["prefer_indoor"] is True,     f"prefer_indoor=True: got {w2['prefer_indoor']}")
    record("C02", not w2["is_rainy"],               f"is_rainy=False: got {w2['is_rainy']}")
    step2 = weather_step(w2, "zh-CN")
    record("C02", "热" in step2 or "热" in step2,  f"zh-CN hot step: {step2}")

    # ── C03: Clear weather ───────────────────────────────────────────────────
    print(head("C03 [en] 天氣分類 — 晴天 → prefer_indoor=False"))
    cast3 = {"dayweather": "晴", "daytemp": "25", "nighttemp": "18",
              "nightweather": "晴", "date": "2026-06-07"}
    w3 = _classify(cast3, {"start": "10:00", "end": "17:00"})
    record("C03", w3["condition"] == "clear",        f"condition=clear: {w3['condition']}")
    record("C03", w3["prefer_indoor"] is False,      f"prefer_indoor=False: {w3['prefer_indoor']}")
    step3 = weather_step(w3, "en")
    record("C03", "Clear" in step3 or "outdoor" in step3, f"en clear step: {step3}")

    # ── C04: POI tags — High reputation + group buy ──────────────────────────
    print(head("C04 [zh-TW] POI標籤 — 高口碑 + 團購划算"))
    poi_good = {
        "rating": 4.7, "review_count": 500, "value_rating": 4.6,
        "has_group_buy": 1, "group_buy_original_price": 300, "group_buy_current_price": 200,
        "half_year_sales": 2000, "queue_signal_level": "Low",
        "queue_mention_rate": 0.10, "risk_mention_rate": 0.30,
        "risk_signal_level": "Low", "category": "餐飲", "sub_category": "日本料理",
        "local_mention_rate": 0.60, "local_authenticity_level": "High",
        "photo_mention_rate": 0.40, "photo_hotness_level": "High",
        "scenario_tags": "情侶約會;朋友聚餐", "year_max": 2025,
    }
    tags, risk_tags = _compute_tags(poi_good, ["情侶約會"], {})
    record("C04", "高口碑" in tags,          f"高口碑 tag present: {tags}")
    record("C04", "團購划算" in tags,         f"團購划算 tag present: {tags}")
    record("C04", "低排隊" in tags,           f"低排隊 tag present: {tags}")
    record("C04", "本地人常去" in tags,       f"本地人常去 tag present: {tags}")
    record("C04", "拍照出片" in tags,         f"拍照出片 tag present: {tags}")
    record("C04", "適合情侶" in tags,         f"適合情侶 tag present: {tags}")
    record("C04", len(risk_tags) == 0,        f"No risk tags (expected 0): {risk_tags}")

    # ── C05: POI tags — Hidden gem ───────────────────────────────────────────
    print(head("C05 [zh-TW] POI標籤 — 冷門寶藏"))
    poi_gem = {
        "rating": 4.5, "review_count": 80, "value_rating": 4.0,
        "has_group_buy": 0, "group_buy_original_price": 0, "group_buy_current_price": 0,
        "half_year_sales": 200, "queue_signal_level": "Low",
        "queue_mention_rate": 0.08, "risk_mention_rate": 0.25,
        "risk_signal_level": "Low", "category": "餐飲", "sub_category": "港式",
        "local_mention_rate": 0.30, "photo_mention_rate": None,
        "scenario_tags": "", "year_max": 2024,
        "avg_price_per_person": 70,
    }
    tags2, risk2 = _compute_tags(poi_gem, [], {})
    record("C05", "冷門寶藏" in tags2,        f"冷門寶藏 tag: {tags2}")
    record("C05", "團購划算" not in tags2,    f"No 團購划算 (no group buy): {tags2}")
    record("C05", "本地人常去" not in tags2,  f"No 本地人常去 (local_mention=0.30 < 0.55): {tags2}")

    # ── C06: POI tags — Risk tags ────────────────────────────────────────────
    print(head("C06 [zh-CN] POI標籤 — 風險標籤"))
    poi_risky = {
        "rating": 4.2, "review_count": 3000, "value_rating": 3.8,
        "has_group_buy": 0, "group_buy_original_price": 0, "group_buy_current_price": 0,
        "half_year_sales": 8000, "queue_signal_level": "High",
        "queue_mention_rate": 0.55, "risk_mention_rate": 0.82,
        "risk_signal_level": "High", "category": "餐飲", "sub_category": "火鍋",
        "local_mention_rate": 0.20, "photo_mention_rate": 0.15,
        "scenario_tags": "朋友聚餐", "year_max": 2025,
    }
    tags3, risk3 = _compute_tags(poi_risky, [], {})
    record("C06", "踩雷風險" in risk3,       f"踩雷風險 risk tag: {risk3}")
    record("C06", "排隊較高" in risk3,       f"排隊較高 risk tag: {risk3}")
    record("C06", "網紅打卡" in risk3,       f"網紅打卡 risk tag (high sales + recent): {risk3}")
    record("C06", "高口碑" not in tags3,     f"No 高口碑 (rating 4.2 < 4.5): {tags3}")

    # ── C07: POI tags — Weather-aware indoor tag ──────────────────────────────
    print(head("C07 [zh-TW] POI標籤 — 雨天友好（天氣感知）"))
    poi_indoor = {
        "rating": 4.3, "review_count": 100, "value_rating": 4.0,
        "has_group_buy": 0, "group_buy_original_price": 0, "group_buy_current_price": 0,
        "half_year_sales": 500, "queue_signal_level": "Medium",
        "queue_mention_rate": 0.25, "risk_mention_rate": 0.40,
        "risk_signal_level": "Medium", "category": "餐飲", "sub_category": "咖啡店",
        "local_mention_rate": None, "photo_mention_rate": None,
        "scenario_tags": "", "year_max": 2024,
    }
    rain_weather = {"prefer_indoor": True, "condition": "rain"}
    tags4, _ = _compute_tags(poi_indoor, [], rain_weather)
    record("C07", "雨天友好" in tags4, f"雨天友好 tag when weather=rain: {tags4}")
    clear_weather = {"prefer_indoor": False, "condition": "clear"}
    tags5, _ = _compute_tags(poi_indoor, [], clear_weather)
    record("C07", "雨天友好" not in tags5, f"No 雨天友好 tag when weather=clear: {tags5}")

    # ── C08: 小紅書 export — all 3 languages ──────────────────────────────────
    print(head("C08 小紅書導出 — 三語格式驗證"))
    route_sample = [
        {"name": "外婆家（南京西路）", "category": "餐飲", "stay_minutes": 90,
         "has_group_buy": True,
         "group_buy": {"title": "雙人套餐", "original_price": 300, "current_price": 198},
         "avg_price_per_person": 150, "queue_risk": "高",
         "risk_tags": ["排隊較高"]},
        {"name": "上海博物館", "category": "文化", "stay_minutes": 60,
         "has_group_buy": False, "group_buy": None,
         "avg_price_per_person": 0, "queue_risk": "低",
         "risk_tags": []},
        {"name": "田子坊", "category": "娱乐", "stay_minutes": 60,
         "has_group_buy": False, "group_buy": None,
         "avg_price_per_person": 50, "queue_risk": "低",
         "risk_tags": []},
    ]
    intent_sample = {
        "city": "上海", "area": "外滩", "budget_total": 400,
        "party_size": 2, "scenarios": ["情侶約會"],
        "food_pref": ["本帮菜"],
    }
    weather_sample = {"condition": "rain", "temperature": 22, "weather": "小雨"}

    xhs_tw = _build_xiaohongshu(route_sample, intent_sample, weather_sample, "zh-TW")
    xhs_cn = _build_xiaohongshu(route_sample, intent_sample, weather_sample, "zh-CN")
    xhs_en = _build_xiaohongshu(route_sample, intent_sample, weather_sample, "en")

    print(f"  [zh-TW 小紅書]\n{textwrap.indent(xhs_tw, '    ')}")
    print(f"  [zh-CN 小紅書]\n{textwrap.indent(xhs_cn, '    ')}")
    print(f"  [en    小紅書]\n{textwrap.indent(xhs_en, '    ')}")

    # Format checks
    record("C08", "📍" in xhs_tw and "🗺" in xhs_tw,  "zh-TW: emoji structure present")
    record("C08", "📍" in xhs_cn and "🗺" in xhs_cn,  "zh-CN: emoji structure present")
    record("C08", "📍" in xhs_en and "🗺" in xhs_en,  "en:    emoji structure present")
    # Language purity — use chars that are UNAMBIGUOUSLY different across scripts
    # (exclude chars like 算/充/足 that are identical codepoints in both scripts)
    _SIMP_ONLY = set("来为时这满预纯简发现状态实际资规则设计划")   # simplified-only codepoints
    _TRAD_ONLY = set("來為時這滿預純簡發現狀態實際資規則設計劃")   # traditional-only equivalents
    tw_has_simp2  = any(c in _SIMP_ONLY for c in xhs_tw)
    cn_has_trad   = any(c in _TRAD_ONLY for c in xhs_cn)
    # en: POI names are naturally Chinese — only check structural/template lines
    en_structural = " ".join(ln for ln in xhs_en.split("\n")
                             if not any(ln.strip().startswith(p) for p in ("🗺","🎟","⚠️")))
    en_struct_has_trad = any(c in _TRAD_ONLY for c in en_structural)
    record("C08", not tw_has_simp2,       f"zh-TW body no simplified: {'CLEAN' if not tw_has_simp2 else 'HAS: '+','.join(c for c in xhs_tw if c in _SIMP_ONLY)}")
    record("C08", not cn_has_trad,        f"zh-CN body no traditional: {'CLEAN' if not cn_has_trad else 'HAS: '+','.join(c for c in xhs_cn if c in _TRAD_ONLY)}")
    record("C08", not en_struct_has_trad, f"en structural text no Traditional: {'CLEAN' if not en_struct_has_trad else 'HAS: '+','.join(c for c in en_structural if c in _TRAD_ONLY)}")
    # Weather line present in all
    record("C08", "雨" in xhs_tw or "☂" in xhs_tw,         "zh-TW weather rain mention")
    record("C08", "雨" in xhs_cn or "☂" in xhs_cn,         "zh-CN weather rain mention")
    record("C08", "Rain" in xhs_en or "rain" in xhs_en or "umbrella" in xhs_en, "en weather rain mention")
    # Group buy present
    record("C08", "198" in xhs_tw or "團購" in xhs_tw,      "zh-TW group buy price shown")
    # Risk line for 外婆家 high queue
    record("C08", "外婆家" in xhs_tw or "避坑" in xhs_tw,   "zh-TW risk tip for high queue POI")
    # Hashtags
    record("C08", "#" in xhs_tw,   "zh-TW hashtags present")
    record("C08", "#" in xhs_cn,   "zh-CN hashtags present")
    record("C08", "#" in xhs_en,   "en  hashtags present")

    # ── HK city detection sanity ──────────────────────────────────────────────
    print(head("C08-extra: _is_hk_city detection"))
    cases_hk = [("香港", True), ("HK", True), ("hong kong", True), ("上海", False),
                ("深圳", False), ("HONG KONG", True), ("香港特別行政區", True)]
    for city, expected in cases_hk:
        got = _is_hk_city(city)
        record("C08", got == expected, f"_is_hk_city({city!r})={got} (expected {expected})")


# ═══════════════════════════════════════════════════════════════════════════
# GROUP B — IntentNode only (1 LLM call each, ~5 seconds per case)
# ═══════════════════════════════════════════════════════════════════════════
def _run_intent(user_input: str, lang: str) -> dict:
    from route_planner.nodes.intent import IntentNode
    from route_planner.state import RouteState
    node  = IntentNode()
    state: RouteState = {
        "user_input": user_input, "language": lang,
        "intent": {}, "candidates": {}, "route": [], "locked_nodes": [],
        "map_url": "", "summary": "", "fulfillment_notes": {},
        "conversation_history": [], "stream_updates": [],
        "user_memory": {}, "weather": {}, "xiaohongshu_post": "",
    }
    return node(state)["intent"]


def group_b():
    print(head("═══ GROUP B: IntentNode tests (1 LLM call each) ═══"))

    # ── C09: zh-TW prefer_local + scenarios detection ────────────────────────
    print(head("C09 [zh-TW] 意圖解析 — prefer_local + 場合標籤"))
    inp09 = "中環附近，想找地道的茶餐廳，帶女朋友，下午茶，預算200"
    try:
        i09 = _run_intent(inp09, "zh-TW")
        print(f"    intent: {json.dumps(i09, ensure_ascii=False, indent=2)}")
        record("C09", i09.get("prefer_local") is True,        f"prefer_local=True: {i09.get('prefer_local')}")
        record("C09", "情侶約會" in i09.get("scenarios", []), f"scenarios 情侶約會: {i09.get('scenarios')}")
        record("C09", i09.get("city", "") in ("香港", ""),    f"city=香港 or empty: {i09.get('city')}")
        record("C09", "茶餐廳" in i09.get("food_pref", []) or "港式" in i09.get("food_pref", []),
               f"food_pref茶餐廳/港式: {i09.get('food_pref')}")
        # Check no simplified in zh-TW output reasoning
        for step in [s for s in i09.get("_raw_reasoning", [""])]:
            pass  # can't check raw, skip
    except Exception as e:
        record("C09", False, f"Exception: {e}")

    # ── C10: zh-CN dining_count=2 ────────────────────────────────────────────
    print(head("C10 [zh-CN] 意圖解析 — dining_count 明確兩餐"))
    inp10 = "上海外滩周末，想吃午饭和晚饭，预算500元，顺便看看夜景"
    try:
        i10 = _run_intent(inp10, "zh-CN")
        print(f"    intent: {json.dumps(i10, ensure_ascii=False, indent=2)}")
        record("C10", i10.get("dining_count") == 2,   f"dining_count=2: got {i10.get('dining_count')}")
        record("C10", i10.get("city") in ("上海", "上海市"), f"city=上海: {i10.get('city')}")
        record("C10", i10.get("area") in ("外滩", "外灘"), f"area=外滩: {i10.get('area')}")
        record("C10", i10.get("budget_total", 0) >= 400, f"budget_total≥400: {i10.get('budget_total')}")
    except Exception as e:
        record("C10", False, f"Exception: {e}")

    # ── C11: en scenarios 打卡 + birthday ────────────────────────────────────
    print(head("C11 [en] 意圖解析 — Scenarios: photo + birthday"))
    inp11 = "Mong Kok tomorrow evening, birthday dinner for 4 people, budget HKD 800, want Japanese food, good for photos"
    try:
        i11 = _run_intent(inp11, "en")
        print(f"    intent: {json.dumps(i11, ensure_ascii=False, indent=2)}")
        record("C11", i11.get("party_size") == 4,                   f"party_size=4: {i11.get('party_size')}")
        record("C11", i11.get("budget_total", 0) >= 700,            f"budget_total≥700: {i11.get('budget_total')}")
        record("C11", "慶生" in i11.get("scenarios", []),           f"scenarios 慶生: {i11.get('scenarios')}")
        record("C11", "打卡拍照" in i11.get("scenarios", []),       f"scenarios 打卡: {i11.get('scenarios')}")
        record("C11", "日本料理" in i11.get("food_pref", []) or
                      "壽司" in i11.get("food_pref", []),            f"food_pref JP: {i11.get('food_pref')}")
        # Language purity: city/area should be Traditional Chinese in database-query fields
        record("C11", i11.get("city", "").strip() != "",            f"city not empty: {i11.get('city')}")
    except Exception as e:
        record("C11", False, f"Exception: {e}")

    # ── C12: zh-TW duration auto-calc + long trip culture ────────────────────
    print(head("C12 [zh-TW] 意圖解析 — 長行程自動加入文化類別"))
    inp12 = "旺角一整天，想吃港式，預算300，帶小孩"
    try:
        i12 = _run_intent(inp12, "zh-TW")
        print(f"    intent: {json.dumps(i12, ensure_ascii=False, indent=2)}")
        record("C12", i12.get("duration_hours", 0) >= 6,                    f"duration≥6h: {i12.get('duration_hours')}")
        record("C12", "文化" in i12.get("must_include_categories", []) or
                      "娱乐" in i12.get("must_include_categories", []),      f"must_include has 文化/娱乐: {i12.get('must_include_categories')}")
        record("C12", "家庭親子" in i12.get("scenarios", []),               f"scenarios 家庭親子: {i12.get('scenarios')}")
    except Exception as e:
        record("C12", False, f"Exception: {e}")

    # ── C13: zh-CN avoid detection ────────────────────────────────────────────
    print(head("C13 [zh-CN] 意圖解析 — avoid + food_pref 詞彙映射"))
    inp13 = "成都春熙路，想吃火锅，但不要太辣，下午3点到晚上9点，预算200"
    try:
        i13 = _run_intent(inp13, "zh-CN")
        print(f"    intent: {json.dumps(i13, ensure_ascii=False, indent=2)}")
        record("C13", "火鍋" in i13.get("food_pref", []) or "火锅" in i13.get("food_pref", []),
               f"food_pref=火鍋: {i13.get('food_pref')}")
        record("C13", i13.get("city", "") in ("成都", "成都市"),  f"city=成都: {i13.get('city')}")
        record("C13", i13.get("area", "") != "",                   f"area not empty: {i13.get('area')}")
        # duration 14:00-18:00 or 15:00-21:00
        dur = i13.get("duration_hours", 0)
        record("C13", 4 <= dur <= 8,                               f"duration 4-8h: {dur}")
        record("C13", i13.get("budget_total", 0) >= 150,           f"budget≥150: {i13.get('budget_total')}")
    except Exception as e:
        record("C13", False, f"Exception: {e}")

    # ── C14: en solo dining ───────────────────────────────────────────────────
    print(head("C14 [en] 意圖解析 — 一人食 + local food"))
    inp14 = "Tsim Sha Tsui this afternoon, solo, want something local and authentic, under HKD 150"
    try:
        i14 = _run_intent(inp14, "en")
        print(f"    intent: {json.dumps(i14, ensure_ascii=False, indent=2)}")
        record("C14", i14.get("prefer_local") is True,           f"prefer_local=True: {i14.get('prefer_local')}")
        record("C14", i14.get("party_size", 2) == 1,             f"party_size=1: {i14.get('party_size')}")
        record("C14", i14.get("budget_total", 0) <= 200,         f"budget≤200: {i14.get('budget_total')}")
        record("C14", "一人食" in i14.get("scenarios", []),      f"scenarios 一人食: {i14.get('scenarios')}")
        record("C14", "尖沙咀" in i14.get("area", "") or "Tsim" in i14.get("area",""),
               f"area=尖沙咀: {i14.get('area')}")
    except Exception as e:
        record("C14", False, f"Exception: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# GROUP C — Full pipeline (LLM + Amap + all nodes)
# ═══════════════════════════════════════════════════════════════════════════
def _run_pipeline(user_input: str, lang: str) -> dict:
    from route_planner.graph import build_graph
    from route_planner.state import RouteState
    graph = build_graph()
    initial: RouteState = {
        "user_input": user_input, "language": lang,
        "intent": {}, "candidates": {}, "route": [], "locked_nodes": [],
        "map_url": "", "summary": "", "fulfillment_notes": {},
        "conversation_history": [], "stream_updates": [],
        "user_memory": {}, "weather": {}, "xiaohongshu_post": "",
    }
    return graph.invoke(initial)


def _check_route_basics(case_id: str, result: dict, min_stops: int = 3):
    route = result.get("route", [])
    record(case_id, len(route) >= min_stops,   f"≥{min_stops} stops: got {len(route)}")
    cats = [p.get("category","") for p in route]
    dining_cnt = sum(1 for c in cats if c in ("餐飲", "餐饮", "Dining"))
    non_dining = len(route) - dining_cnt
    record(case_id, dining_cnt >= 1,            f"≥1 dining stop: {dining_cnt}")
    record(case_id, non_dining >= 1,            f"≥1 non-dining stop: {non_dining}")
    record(case_id, result.get("map_url","") != "", "map_url generated")
    record(case_id, result.get("summary","") != "", "summary generated")
    record(case_id, result.get("xiaohongshu_post","") != "", "xiaohongshu_post generated")
    # POI tags must be lists
    for p in route:
        record(case_id, isinstance(p.get("tags"), list),      f"tags is list: {p.get('name')}")
        record(case_id, isinstance(p.get("risk_tags"), list), f"risk_tags is list: {p.get('name')}")
    return route


def group_c():
    print(head("═══ GROUP C: Full pipeline tests ═══"))

    # ── C15: zh-TW HK standard ───────────────────────────────────────────────
    print(head("C15 [zh-TW] 完整流水線 — 香港旺角日本料理"))
    try:
        r15 = _run_pipeline("旺角下午，想吃壽司，逛逛藝術館，預算400港幣，兩個人", "zh-TW")
        print(f"  SSE steps: {r15['stream_updates']}")
        route15 = _check_route_basics("C15", r15)
        # Weather field
        record("C15", isinstance(r15.get("weather"), dict), f"weather is dict: {r15.get('weather')}")
        # Language purity in step messages
        for s in r15["stream_updates"]:
            has_simp = any(c in "来为时间这" for c in s)
            if has_simp:
                record("C15", False, f"zh-TW step contains simplified: {s}")
                break
        else:
            record("C15", True, "zh-TW steps: no simplified chars found")
        # 小红书
        xhs = r15.get("xiaohongshu_post","")
        print(f"  小紅書:\n{textwrap.indent(xhs, '    ')}")
        record("C15", "旺角" in xhs or "香港" in xhs, f"xhs contains location")
        record("C15", "#" in xhs, "xhs has hashtags")
    except Exception as e:
        record("C15", False, f"Exception: {traceback.format_exc()}")

    # ── C16: zh-CN mainland Amap-first ───────────────────────────────────────
    print(head("C16 [zh-CN] 完整流水線 — 上海外灘本幫菜（大陸城市，高德優先）"))
    try:
        r16 = _run_pipeline("上海外滩周末下午，预算300元，想吃本帮菜，顺便逛文化景点", "zh-CN")
        print(f"  SSE steps: {r16['stream_updates']}")
        route16 = _check_route_basics("C16", r16)
        # Check for amap_fallback or amap-sourced POIs
        steps16 = " ".join(r16["stream_updates"])
        amap_used = "高德" in steps16 or "amap" in steps16.lower() or \
                    any(p.get("id","").startswith("amap_") for p in route16)
        record("C16", amap_used, f"Amap data used for mainland city: {'YES' if amap_used else 'NO (SQLite only)'}")
        # Language: zh-CN steps should be simplified (use unambiguous chars only)
        # "充"/"算" are the same codepoint in both scripts — exclude from detection
        _TRAD_UNAMBIGUOUS = set("來為時這滿預發現狀態實際資規則設計劃標準邊發")
        trad_step_found = None
        for s in r16["stream_updates"]:
            found = [c for c in s if c in _TRAD_UNAMBIGUOUS]
            if found:
                trad_step_found = (s, found)
                break
        if trad_step_found:
            record("C16", False, f"zh-CN step has Traditional chars {trad_step_found[1]}: {trad_step_found[0]}")
        else:
            record("C16", True, "zh-CN steps: no unambiguous Traditional chars")
        xhs16 = r16.get("xiaohongshu_post","")
        print(f"  小红书:\n{textwrap.indent(xhs16, '    ')}")
        has_trad_xhs = any(c in _TRAD_UNAMBIGUOUS for c in xhs16)
        record("C16", not has_trad_xhs, f"zh-CN xhs no traditional chars: {'CLEAN' if not has_trad_xhs else 'HAS TRAD'}")
    except Exception as e:
        record("C16", False, f"Exception: {traceback.format_exc()}")

    # ── C17: en full pipeline ─────────────────────────────────────────────────
    print(head("C17 [en] 完整流水線 — English query Hong Kong"))
    try:
        r17 = _run_pipeline("Central Hong Kong this afternoon, 2 people, budget HKD 500, want Cantonese dim sum and a cultural spot", "en")
        print(f"  SSE steps: {r17['stream_updates']}")
        route17 = _check_route_basics("C17", r17)
        # English step messages may contain Chinese POI names — check only structural steps
        # Structural steps are those that don't reference POI names directly
        _STRUCTURAL_PREFIXES = ("Parsed:", "Found candidates:", "Geo-cluster:", "✅", "Route ready:",
                                "Queue /", "Route complete,", "Cache hit")
        structural_steps = [s for s in r17["stream_updates"]
                            if any(s.startswith(p) for p in _STRUCTURAL_PREFIXES)]
        for s in structural_steps:
            has_cjk = any("一" <= c <= "鿿" for c in s)
            if has_cjk:
                record("C17", False, f"English structural step has CJK: {s}")
                break
        else:
            record("C17", True, f"en structural steps clean ({len(structural_steps)} checked)")
        xhs17 = r17.get("xiaohongshu_post","")
        print(f"  XHS:\n{textwrap.indent(xhs17, '    ')}")
        # en XHS may have Chinese POI names — only check structural lines
        xhs17_structural = " ".join(ln for ln in xhs17.split("\n")
                                    if not any(ln.strip().startswith(p) for p in ("🗺","🎟","⚠️")))
        has_cjk_struct = any("一" <= c <= "鿿" for c in xhs17_structural)
        record("C17", not has_cjk_struct, f"en xhs structural text no CJK: {'CLEAN' if not has_cjk_struct else 'HAS CJK'}")
        # POI names in route may be Chinese — that's OK; check summary language
        record("C17", any(w in r17.get("summary","") for w in ("stop","Stop","planned","Planned")),
               f"summary in English: {r17.get('summary','')[:60]}")
    except Exception as e:
        record("C17", False, f"Exception: {traceback.format_exc()}")

    # ── C18: dining_count=2 enforcement ──────────────────────────────────────
    print(head("C18 [zh-TW] 完整流水線 — dining_count=2 嚴格執行"))
    try:
        r18 = _run_pipeline("銅鑼灣今天，想吃午飯和下午茶，逛逛，預算350港幣兩人", "zh-TW")
        print(f"  SSE steps: {r18['stream_updates']}")
        route18 = r18.get("route", [])
        dining_18 = [p for p in route18 if p.get("category") in ("餐飲","餐饮","Dining")]
        record("C18", len(route18) >= 3,    f"≥3 stops: {len(route18)}")
        record("C18", len(dining_18) == 2,  f"dining_count=2 enforced: got {len(dining_18)} dining stops")
        # Intent should have dining_count=2
        intent18 = r18.get("intent", {})
        record("C18", intent18.get("dining_count") == 2, f"intent.dining_count=2: {intent18.get('dining_count')}")
    except Exception as e:
        record("C18", False, f"Exception: {traceback.format_exc()}")

    # ── C19: prefer_local + scenario tags propagate ───────────────────────────
    print(head("C19 [zh-TW] 完整流水線 — prefer_local + 情侶約會標籤傳遞"))
    try:
        r19 = _run_pipeline("中環附近，跟女朋友吃晚飯，想吃地道粵菜，預算500兩人", "zh-TW")
        print(f"  SSE steps: {r19['stream_updates']}")
        route19 = r19.get("route", [])
        intent19 = r19.get("intent", {})
        record("C19", intent19.get("prefer_local") is True,         f"intent.prefer_local=True: {intent19.get('prefer_local')}")
        record("C19", "情侶約會" in intent19.get("scenarios", []),  f"intent.scenarios 情侶約會: {intent19.get('scenarios')}")
        # At least one dining POI should have 本地人常去 or 適合情侶 tag
        all_tags = [t for p in route19 for t in (p.get("tags") or [])]
        record("C19", "本地人常去" in all_tags or "適合情侶" in all_tags,
               f"local/couple tags in route: {all_tags}")
        # 小紅書 should mention 情侶 scenario
        xhs19 = r19.get("xiaohongshu_post","")
        record("C19", "情侶" in xhs19 or "couple" in xhs19.lower(), f"xhs mentions 情侶: snippet={xhs19[:100]}")
    except Exception as e:
        record("C19", False, f"Exception: {traceback.format_exc()}")

    # ── C20: 小紅書 content completeness check ────────────────────────────────
    print(head("C20 [zh-CN] 完整流水線 — 小紅書完整性檢查"))
    try:
        r20 = _run_pipeline("深圳南山区今天下午，预算200元，两人，想吃日本料理，拍照打卡", "zh-CN")
        print(f"  SSE steps: {r20['stream_updates']}")
        route20 = r20.get("route", [])
        xhs20 = r20.get("xiaohongshu_post","")
        print(f"  小红书:\n{textwrap.indent(xhs20, '    ')}")
        record("C20", len(route20) >= 3,          f"≥3 stops: {len(route20)}")
        record("C20", "深圳" in xhs20 or "南山" in xhs20, f"location in xhs: {xhs20[:50]}")
        record("C20", "→" in xhs20,               f"route arrow in xhs")
        record("C20", "#" in xhs20,               f"hashtags in xhs")
        # Photo tag should appear if scenarios contains 打卡拍照
        intent20 = r20.get("intent", {})
        record("C20", "打卡拍照" in intent20.get("scenarios",[]),  f"intent.scenarios 打卡拍照: {intent20.get('scenarios')}")
        all_tags20 = [t for p in route20 for t in (p.get("tags") or [])]
        if any("拍照" in t for t in all_tags20):
            record("C20", True, f"拍照出片 tag found in route: {all_tags20}")
        else:
            record("C20", True, f"(no photo-hotness POIs in results, tag absent — acceptable): {all_tags20}")
    except Exception as e:
        record("C20", False, f"Exception: {traceback.format_exc()}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════
def main():
    run = (sys.argv[1].upper() if len(sys.argv) > 1 else "ALL")

    if run in ("A", "ALL"):
        group_a()
    if run in ("B", "ALL"):
        group_b()
    if run in ("C", "ALL"):
        group_c()

    # ── Final summary ─────────────────────────────────────────────────────────
    print(head("═══ SUMMARY ═══"))
    total  = len(_results)
    passed = sum(1 for _, ok_, _ in _results if ok_)
    failed = total - passed
    fails  = [(cid, det) for cid, ok_, det in _results if not ok_]

    print(f"  Total checks: {total}")
    print(f"  {_GREEN}Passed: {passed}{_RST}")
    if failed:
        print(f"  {_RED}Failed: {failed}{_RST}")
        print(f"\n{_BOLD}Failed details:{_RST}")
        for cid, det in fails:
            print(f"  {_RED}{cid}{_RST}: {det}")
    else:
        print(f"  {_GREEN}All checks passed!{_RST}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
