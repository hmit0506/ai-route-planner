import json
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.llm import call_llm

_SYSTEM_PROMPT = """\
你是一个本地路线规划助手。根据用户意图和候选POI，选出最优路线。

输出必须是严格的JSON数组，每个元素格式：
{"poi_id": "poi_xxx", "order": 1, "stay_minutes": 90}

规划原则：
- 站点数量以 max_pois 为参考，可根据行程丰富度灵活调整，但最少3站
- 若用户有明确的 meal_plan（如["午饭","晚饭"]），餐饮站点数量必须匹配，不多不少
- 若用户没有明确 meal_plan，餐饮站点不超过总站数的40%，保证行程多样性
- 按游览顺序排列（lat/lng越近越好，避免来回折腾）
- stay_minutes参考：正餐60-120，博物馆/景点60-90，书店/街区30-60，咖啡/茶饮20-40
- 所有stay_minutes之和控制在 duration_hours×60 的75%以内（留出交通时间）
- 预算优先用group_buy_price（团购实付价），无团购才用avg_price_per_person；总价不超budget_per_person
- 预算有限时优先选value_rating高的POI；review_count < 100时评分可信度低，谨慎选入
- queue_minutes_peak > 30 但 queue_minutes_offpeak <= 15 时可安排在非高峰时段
- 餐饮类优先参考taste_rating；half_year_sales越高越热门，同等条件下优先高销量
- 只输出JSON数组，不要有任何额外文字或解释
"""

_CORRECTION_PROMPT = """\
你上一次的选择存在问题：{reason}

请重新选择，严格遵守上述规则，只输出JSON数组。
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
        "review_count": poi.get("review_count", 0),
        "value_rating": poi.get("value_rating", 0),
        "avg_price_per_person": poi.get("avg_price_per_person", 0),
        "group_buy_price": gb_price,
        "queue_risk": poi.get("queue_risk", "低"),
        "queue_minutes_peak": poi.get("queue_minutes_peak", 0),
        "queue_minutes_offpeak": poi.get("queue_minutes_offpeak", 0),
        "half_year_sales": poi.get("half_year_sales", 0),
        "business_hours": poi.get("business_hours", ""),
        "lat": poi.get("lat", 0),
        "lng": poi.get("lng", 0),
    }
    if poi.get("category") == "餐饮":
        result["taste_rating"] = poi.get("taste_rating", 0)
    return result


def _validate(selection: list, intent: dict) -> str | None:
    """Return error string if selection violates constraints, else None."""
    if not selection:
        return "路线为空，至少需要3个地点"

    if len(selection) < 3:
        return f"只选了{len(selection)}站，至少需要3站"

    categories = [s.get("category", "") for s in selection]
    dining_count = categories.count("餐饮")
    non_dining = len(selection) - dining_count

    meal_plan = intent.get("meal_plan", [])

    if meal_plan:
        # User specified exact meals: dining count must match
        if dining_count != len(meal_plan):
            return (
                f"用户明确要求{len(meal_plan)}顿饭（{meal_plan}），"
                f"但选了{dining_count}个餐饮站点，数量不符"
            )
    else:
        # No explicit meal plan: ensure at least 1 non-dining spot
        if non_dining == 0:
            return "路线全是餐饮，缺少文化/娱乐/自然类站点"
        if dining_count > len(selection) * 0.5 and len(selection) >= 4:
            return (
                f"餐饮站点占比过高（{dining_count}/{len(selection)}），"
                f"行程缺乏多样性，请减少餐饮、增加文化娱乐活动"
            )

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
        meal_plan = intent.get("meal_plan", [])

        # Build clean user context (exclude internal GeoCluster fields)
        _exclude = {"max_pois", "max_dining", "min_cultural", "_refine"}
        user_intent = {k: v for k, v in intent.items() if k not in _exclude}

        meal_note = (
            f"用户明确要求的餐饮：{meal_plan}（餐饮站点数量必须恰好为{len(meal_plan)}个）"
            if meal_plan else
            "用户未指定具体餐次，餐饮站点不超过总站数的40%"
        )

        user_msg = (
            f"用户意图：{json.dumps(user_intent, ensure_ascii=False)}\n\n"
            f"时间预算：{duration_hours}小时，参考站数{max_pois}站\n"
            f"餐饮约束：{meal_note}\n\n"
            f"候选POI（已按地理聚合过滤）：\n"
            f"{json.dumps(compact_candidates, ensure_ascii=False, indent=2)}\n\n"
            "请选出最优路线，只输出JSON数组。"
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        selection = call_llm(messages, parse_json=True)

        # Enrich selection with category info for validation
        poi_lookup = {p["id"]: p for pois in candidates.values() for p in pois}
        for item in selection:
            if "category" not in item:
                poi = poi_lookup.get(item.get("poi_id", ""), {})
                item["category"] = poi.get("category", "")

        # Self-check: validate and retry once if needed
        error = _validate(selection, intent)
        if error:
            correction_msg = {
                "role": "assistant",
                "content": json.dumps(selection, ensure_ascii=False),
            }
            retry_msg = {
                "role": "user",
                "content": _CORRECTION_PROMPT.format(reason=error),
            }
            selection = call_llm(messages + [correction_msg, retry_msg], parse_json=True)
            # Re-enrich after retry
            for item in selection:
                if "category" not in item:
                    poi = poi_lookup.get(item.get("poi_id", ""), {})
                    item["category"] = poi.get("category", "")

        updates = list(state.get("stream_updates", []))
        updates.append(f"路线生成完成，共{len(selection)}个地点")

        return {**state, "route": selection, "stream_updates": updates}
