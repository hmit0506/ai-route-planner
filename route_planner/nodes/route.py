import json
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.llm import call_llm
from route_planner.user_memory import build_route_hint
import route_planner.i18n as i18n

_SYSTEM_PROMPT = """\
你是一个本地路线规划助手。根据用户意图和候选POI，选出最优路线。

输出必须是严格的JSON数组，每个元素格式：
{"poi_id": "poi_xxx", "order": 1, "stay_minutes": 90}

规划原则：
- 站点数量以 max_pois 为参考，可根据行程丰富度灵活调整，但最少3站
- 若 dining_count > 0，餐饮站点数量必须恰好等于 dining_count，不多不少
- 若 dining_count == 0，餐饮站点合理安排即可，保证行程有非餐饮类站点
- 按游览顺序排列（lat/lng越近越好，避免来回折腾）
- stay_minutes参考：正餐60-120，博物馆/景点60-90，书店/街区30-60，咖啡/茶饮20-40
- 所有stay_minutes之和控制在 duration_hours×60 的75%以内（留出交通时间）
- 预算优先用group_buy_price（团购实付价），无团购才用avg_price_per_person；总价不超budget_per_person
- 预算有限时优先选value_rating高的POI；recommend_count越高口碑越好，可作为次要排序依据
- queue_minutes_peak > 30 但 queue_minutes_offpeak <= 15 时可安排在非高峰时段
- 餐饮类优先参考taste_rating和hygiene_rating；环境体验类（文化/娱乐）优先参考decor_rating和service_rating；half_year_sales越高越热门，recommend_count越高口碑越好，trend_tag为"火爆"优先于"经典"优先于"新晋"，review_count越高评分越可信
- 用户的 food_pref（菜系偏好）和 culture_pref（文化偏好）是选站的首要依据：餐饮站点的 sub_category 应尽量匹配 food_pref，文化/娱乐站点的 sub_category 应尽量匹配 culture_pref
- 评论信号字段（来自真实用户评论，优先级高于模拟字段，全部应纳入决策）：
  - year_max (整数2021~2025): 最近一次收到评论的年份。2025=近期仍活跃；<=2022=四年内无新评论，可能已关或口碑下滑，若候选中有更新的选项应降低权重
  - risk_mention_rate (0~1浮点，全量均值0.6): 负面体验短语占比，低于0.4为优秀，高于0.8有踩雷风险；优先选值更低的POI
  - queue_mention_rate (0~1浮点，均值0.3): 排队抱怨占比，高于0.5意味着明显排队问题，需在路线安排中考虑（如调整到达时间）
  - local_mention_rate (0~1浮点，均值0.39): 地道/本土感短语占比，值越高越地道；若用户 prefer_local=true 应优先选高值POI
  - photo_mention_rate (0~1浮点，均值0.23): 拍照/打卡短语占比，若用户 scenarios 含"打卡拍照"应优先选高值POI
  - accessibility_mention_rate (0~1浮点，均值0.24): 无障碍/可达性短语占比，若 scenarios 含"家庭親子"应适当偏好高值POI
  - risk_signal_level / queue_signal_level: 三等分位标签（Low/Medium/High），可辅助确认 float 值的相对位置
  - scenario_tags: 场合标签（如"情侶約會;朋友聚餐"），与用户 scenarios 匹配时加分
- 天气感知路线规则（当 intent.weather 存在时必须遵守）：
  - condition=rain/storm：必须减少户外停留，博物馆/艺术馆/室内餐厅优先；公园/citywalk/户外景点降权或不选
  - condition=hot（温度>=33°C）：减少户外暴晒站点，优先商场/咖啡厅/室内文化场所
  - condition=cold（温度<=10°C）：减少户外停留时长，优先室内场所
  - condition=clear：可正常推荐户外活动
  - prefer_indoor=true：在候选中优先选 category 为餐饮/文化/室内的 POI，回避公园、citywalk 类
- 只输出JSON数组，不要有任何额外文字或解释
"""

_CORRECTION_PROMPT = """\
你上一次的选择存在问题：{reason}

{extra}请重新选择，只输出JSON数组，不要有任何额外文字。
"""


def _compact(poi: dict) -> dict:
    gb_price = poi.get("group_buy_current_price") if poi.get("has_group_buy") else None
    result = {
        "poi_id": poi["id"],
        "name": poi["name"],
        "category": poi["category"],
        "sub_category": poi.get("sub_category", ""),
        "area": poi.get("area", ""),
        "rating": poi.get("rating", 0),
        "value_rating": poi.get("value_rating", 0),
        "hygiene_rating": poi.get("hygiene_rating", 0),
        "avg_price_per_person": poi.get("avg_price_per_person", 0),
        "group_buy_price": gb_price,
        "queue_risk": poi.get("queue_risk", "低"),
        "queue_minutes_peak": poi.get("queue_minutes_peak", 0),
        "queue_minutes_offpeak": poi.get("queue_minutes_offpeak", 0),
        "half_year_sales": poi.get("half_year_sales", 0),
        "recommend_count": poi.get("recommend_count", 0),
        "review_count": poi.get("review_count", 0),
        "trend_tag": poi.get("trend_tag", ""),
        "business_hours": poi.get("business_hours", ""),
        "lat": poi.get("lat", 0),
        "lng": poi.get("lng", 0),
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
    }
    if poi.get("category") in {"餐饮", "Dining", "餐飲"}:
        result["taste_rating"] = poi.get("taste_rating", 0)
    else:
        result["decor_rating"]   = poi.get("decor_rating", 0)
        result["service_rating"] = poi.get("service_rating", 0)
    return result


_VALIDATE_MSGS = {
    "zh-CN": {
        "empty":       "路线为空，至少需要3个地点",
        "too_few":     "只选了{n}站，至少需要3站",
        "dining_mismatch": "用户明确要求{exp}个餐饮活动，但选了{got}个餐饮站点，数量不符",
        "no_culture":  "路线全是餐饮，缺少文化/娱乐/自然类站点",
    },
    "zh-TW": {
        "empty":       "路線為空，至少需要3個地點",
        "too_few":     "只選了{n}站，至少需要3站",
        "dining_mismatch": "用戶明確要求{exp}個餐飲活動，但選了{got}個餐飲站點，數量不符",
        "no_culture":  "路線全是餐飲，缺少文化/娛樂/自然類站點",
    },
    "en": {
        "empty":       "No stops selected, need at least 3",
        "too_few":     "Only {n} stop(s) selected, need at least 3",
        "dining_mismatch": "User requested {exp} dining stop(s), but {got} were selected",
        "no_culture":  "Route is all dining, missing cultural/entertainment stops",
    },
}


def _validate(selection: list, intent: dict, poi_lookup: dict, lang: str = "zh-TW") -> str | None:
    msgs = _VALIDATE_MSGS.get(i18n.normalize(lang), _VALIDATE_MSGS["zh-TW"])
    if not selection:
        return msgs["empty"]
    if len(selection) < 3:
        return msgs["too_few"].format(n=len(selection))

    dining_count = sum(1 for s in selection if s.get("category") in {"餐饮", "Dining", "餐飲"})
    non_dining = len(selection) - dining_count
    expected_dining = intent.get("dining_count", 0)

    if expected_dining > 0:
        # Only enforce dining_count when non-dining candidates exist to fill the remaining stops
        all_pois = list(poi_lookup.values())
        has_non_dining_candidates = any(
            p.get("category") not in {"餐饮", "Dining", "餐飲"} for p in all_pois
        )
        if dining_count != expected_dining and has_non_dining_candidates:
            return msgs["dining_mismatch"].format(exp=expected_dining, got=dining_count)
    else:
        all_pois = list(poi_lookup.values())
        has_non_dining_candidates = any(p.get("category") not in {"餐饮", "Dining", "餐飲"} for p in all_pois)
        if non_dining == 0 and has_non_dining_candidates:
            return msgs["no_culture"]

    return None


class RouteNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        intent = state["intent"]
        candidates = state["candidates"]

        compact_candidates = {
            cat: [_compact(p) for p in pois]
            for cat, pois in candidates.items()
        }

        max_pois = intent.get("max_pois", 4)
        duration_hours = intent.get("duration_hours", 4)
        dining_count_req = intent.get("dining_count", 0)

        # Build clean user context (exclude internal GeoCluster fields)
        _exclude = {"max_pois", "max_dining", "min_cultural", "_refine"}
        user_intent = {k: v for k, v in intent.items() if k not in _exclude}

        food_pref = intent.get("food_pref", [])
        culture_pref = intent.get("culture_pref", [])

        meal_note = (
            f"用户明确要求{dining_count_req}个餐饮活动（餐饮站点数量必须恰好为{dining_count_req}个）"
            if dining_count_req > 0 else
            "用户未指定餐次数量，合理安排即可"
        )
        pref_note = ""
        if food_pref:
            pref_note += f"- 餐饮偏好：{food_pref}，优先选 sub_category 最接近的；数据库无完全匹配时选评分最高的餐饮\n"
        if culture_pref:
            pref_note += f"- 文化偏好：{culture_pref}，优先选 sub_category 最接近的；数据库无完全匹配时选评分最高的文化/娱乐\n"

        memory_hint = build_route_hint(state.get("user_memory", {}), intent)

        weather = intent.get("weather", {})
        weather_note = ""
        if weather:
            cond = weather.get("condition", "clear")
            temp = weather.get("temperature", 0)
            desc = weather.get("weather", "")
            prefer_indoor_flag = weather.get("prefer_indoor", False)
            weather_note = (
                f"当前天气：{desc}，{int(temp)}°C，condition={cond}，prefer_indoor={prefer_indoor_flag}\n"
                f"⚠️ 请严格按天气感知规则调整路线。\n"
            )

        user_msg = (
            f"用户意图：{json.dumps(user_intent, ensure_ascii=False)}\n\n"
            + (f"🌤 天气信息：\n{weather_note}" if weather_note else "")
            + f"时间预算：{duration_hours}小时，参考站数{max_pois}站\n"
            f"餐饮安排：{meal_note}\n"
            + (f"偏好参考（尽量满足，无匹配时选最近似）：\n{pref_note}" if pref_note else "")
            + (f"\n{memory_hint}\n" if memory_hint else "")
            + f"\n候选POI（已按地理聚合过滤，偏好匹配的已排前）：\n"
            f"{json.dumps(compact_candidates, ensure_ascii=False, indent=2)}\n\n"
            "请选出最优路线，只输出JSON数组。"
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        selection = call_llm(messages, parse_json=True)

        # Build poi_lookup for validation and category enrichment
        poi_lookup = {p["id"]: p for pois in candidates.values() for p in pois}
        for item in selection:
            if "category" not in item:
                poi = poi_lookup.get(item.get("poi_id", ""), {})
                item["category"] = poi.get("category", "")

        lang = state.get("language", "zh-TW")
        updates = list(state.get("stream_updates", []))

        _DINING_CATS = {"餐饮", "Dining", "餐飲"}

        def _enrich_category(sel: list) -> None:
            for item in sel:
                if "category" not in item:
                    poi = poi_lookup.get(item.get("poi_id", ""), {})
                    item["category"] = poi.get("category", "")

        def _force_dining_count(sel: list, expected: int) -> list:
            """Code-level enforcement: trim or leave dining stops to match expected."""
            dining  = [s for s in sel if s.get("category") in _DINING_CATS]
            non_din = [s for s in sel if s.get("category") not in _DINING_CATS]
            if len(dining) > expected:
                # Keep highest-rated dining stops
                dining.sort(key=lambda x: -poi_lookup.get(x.get("poi_id",""), {}).get("rating", 0))
                dining = dining[:expected]
            result = dining + non_din
            for idx, item in enumerate(result, 1):
                item["order"] = idx
            return result

        # Self-check: validate and retry once if needed
        _enrich_category(selection)
        error = _validate(selection, intent, poi_lookup, lang)
        if error:
            updates.append(i18n.step("route_warn", lang, reason=error))
            # Build a more specific correction hint for dining_count violations
            expected_dining = intent.get("dining_count", 0)
            if expected_dining > 0:
                dining_ids = [p["id"] for pois in candidates.values() for p in pois
                              if p.get("category") in _DINING_CATS]
                extra = (
                    f"特别注意：餐饮站点数量必须恰好为 {expected_dining} 个。"
                    f"候选中的餐饮POI id为：{dining_ids[:10]}。"
                    f"从中选恰好 {expected_dining} 个，其余站点选非餐饮类POI。\n"
                )
            else:
                extra = ""
            correction_msg = {
                "role": "assistant",
                "content": json.dumps(selection, ensure_ascii=False),
            }
            retry_msg = {
                "role": "user",
                "content": _CORRECTION_PROMPT.format(reason=error, extra=extra),
            }
            selection_before_retry = list(selection)  # keep original in case retry is worse
            selection = call_llm(messages + [correction_msg, retry_msg], parse_json=True)
            _enrich_category(selection)
            # Code-level enforcement: if dining_count still wrong after retry, force it
            if expected_dining > 0:
                actual = sum(1 for s in selection if s.get("category") in _DINING_CATS)
                if actual != expected_dining:
                    selection = _force_dining_count(selection, expected_dining)
                # Safety: if force produced empty, fall back to original selection
                if not selection:
                    selection = selection_before_retry
        else:
            updates.append(i18n.step("route_ok", lang))

        updates.append(i18n.step("route_done", lang, n=len(selection)))

        return {**state, "route": selection, "stream_updates": updates}
