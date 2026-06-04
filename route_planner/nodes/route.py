import json
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.llm import call_llm

_SYSTEM_PROMPT = """\
你是一个本地路线规划助手。根据用户意图和候选POI，选出最优路线。

输出必须是严格的JSON数组，每个元素格式：
{"poi_id": "poi_xxx", "order": 1, "stay_minutes": 90}

规则：
- 站点数量以 max_pois 为参考，可根据行程丰富度±1站灵活调整，但最少3站
- 至少包含1个餐饮类和1个文化/娱乐/自然类
- 按游览顺序排列（lat/lng越近越好，减少来回折腾）
- stay_minutes参考：餐饮60-120，博物馆/景点60-90，书店/街区30-60，咖啡/奶茶20-40
- 所有stay_minutes之和控制在 duration_hours×60 的75%以内（留出交通时间）
- 预算优先用group_buy_price（团购实付价），无团购才用avg_price_per_person；总价不超budget_per_person
- 预算有限时优先选value_rating高的POI（性价比好）；review_count少于100的POI评分可信度低，谨慎选入
- queue_minutes_peak > 30 但 queue_minutes_offpeak <= 15 的POI可安排在非高峰时段（开场或14:00前）；两者都高则降低优先级
- 餐饮类优先参考taste_rating；half_year_sales越高说明越热门，同等条件下优先高销量
- 只输出JSON数组，不要有任何额外文字或解释
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
        "review_count": poi.get("review_count", 0),          # 评价数：权衡评分可信度
        "value_rating": poi.get("value_rating", 0),          # 性价比评分：预算敏感用户优先考虑
        "avg_price_per_person": poi.get("avg_price_per_person", 0),
        "group_buy_price": gb_price,
        "queue_risk": poi.get("queue_risk", "低"),
        "queue_minutes_peak": poi.get("queue_minutes_peak", 0),
        "queue_minutes_offpeak": poi.get("queue_minutes_offpeak", 0),  # 非高峰等位：决定是否可错峰安排
        "half_year_sales": poi.get("half_year_sales", 0),
        "business_hours": poi.get("business_hours", ""),
        "lat": poi.get("lat", 0),
        "lng": poi.get("lng", 0),
    }
    # 餐饮类额外附上口味评分，是食客最核心关注点
    if poi.get("category") == "餐饮":
        result["taste_rating"] = poi.get("taste_rating", 0)
    return result


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

        user_msg = (
            f"用户意图：{json.dumps(intent, ensure_ascii=False)}\n\n"
            f"时间约束：总行程{duration_hours}小时，最多选{max_pois}站（含交通时间）\n\n"
            f"候选POI（已按地理聚合过滤，每类最多10个）：\n"
            f"{json.dumps(compact_candidates, ensure_ascii=False, indent=2)}\n\n"
            f"请选出最优路线，严格不超过{max_pois}站。"
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        selection = call_llm(messages, parse_json=True)

        updates = list(state.get("stream_updates", []))
        updates.append(f"路线生成完成，共{len(selection)}个地点")

        return {**state, "route": selection, "stream_updates": updates}
