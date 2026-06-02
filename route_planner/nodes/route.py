import json
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.llm import call_llm

_SYSTEM_PROMPT = """\
你是一个本地路线规划助手。根据用户意图和候选POI，选出最优路线（3-5个地点）。

输出必须是严格的JSON数组，每个元素格式：
{"poi_id": "poi_xxx", "order": 1, "stay_minutes": 90}

规则：
- 至少包含1个餐饮类和1个文化/娱乐/自然类
- 按游览顺序排列（地理位置尽量相邻，减少来回折腾）
- stay_minutes参考：餐饮60-120，博物馆/景点60-90，书店/街区30-60，咖啡/奶茶20-40
- 各POI的avg_price_per_person之和不超过budget_per_person
- 高排队风险的地点优先安排在非高峰时段（开场前或14:00前到达）
- 只输出JSON数组，不要有任何额外文字或解释
"""


def _compact(poi: dict) -> dict:
    return {
        "poi_id": poi["id"],
        "name": poi["name"],
        "category": poi["category"],
        "sub_category": poi.get("sub_category", ""),
        "area": poi.get("area", ""),
        "rating": poi.get("rating", 0),
        "avg_price_per_person": poi.get("avg_price_per_person", 0),
        "queue_risk": poi.get("queue_risk", "低"),
        "has_group_buy": poi.get("has_group_buy", False),
        "business_hours": poi.get("business_hours", ""),
        "lat": poi.get("lat", 0),
        "lng": poi.get("lng", 0),
    }


class RouteNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        intent = state["intent"]
        candidates = state["candidates"]

        compact_candidates = {
            cat: [_compact(p) for p in pois]
            for cat, pois in candidates.items()
        }

        user_msg = (
            f"用户意图：{json.dumps(intent, ensure_ascii=False)}\n\n"
            f"候选POI（按评分排序，每类最多10个）：\n"
            f"{json.dumps(compact_candidates, ensure_ascii=False, indent=2)}\n\n"
            "请选出最优路线（3-5个地点）。"
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        selection = call_llm(messages, parse_json=True)

        updates = list(state.get("stream_updates", []))
        updates.append(f"路线生成完成，共{len(selection)}个地点")

        return {**state, "route": selection, "stream_updates": updates}
