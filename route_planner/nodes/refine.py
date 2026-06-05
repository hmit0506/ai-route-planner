"""
RefineNode: LLM call that parses a "swap one POI" request.

Reads the current route from state["route"] and user_input, then outputs:
  intent["_refine"] = {
      "replace_order": int,          # 1-indexed order of POI to swap
      "category": str,               # category of the POI to swap
      "new_constraints": {           # optional extra filters for the replacement search
          "queue_risk": "低"|"中"|"高",
          "max_price": int,
          "avoid_sub_category": [str]
      }
  }
Also sets state["locked_nodes"] = all order-indices except the one being replaced.
"""
import json
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.llm import call_llm

_SYSTEM_PROMPT = """\
你是一个路线规划助手，负责处理用户的局部替换请求。
用户有一条现有路线，想替换其中某一个地点。

根据用户的消息和当前路线，输出严格的JSON，格式如下：
{
  "replace_order": <整数，1开始，表示要替换第几个地点>,
  "category": "<要替换的地点类别，如 餐饮/文化/娱乐>",
  "new_constraints": {
    "queue_risk": "<可选：低/中/高，用户要求等位少时填'低'>",
    "max_price": <可选：整数，用户要求便宜时填写人均上限>,
    "avoid_sub_category": ["<可选：要排除的子类别>"],
    "prefer_sub_category": ["<可选：用户指定的菜系或类型，如 日本料理、壽司、博物館>"]
  }
}

规则：
- 若用户没有指定替换哪个，默认替换评分最低或排队风险最高的那个
- new_constraints 中不需要的字段可省略
- 只输出 JSON，不要有任何额外文字
"""


class RefineNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        user_input = state["user_input"]
        route = state.get("route", [])
        intent = dict(state.get("intent", {}))

        route_summary = json.dumps(
            [
                {
                    "order": p.get("order", i + 1),
                    "name": p.get("name", ""),
                    "category": p.get("category", ""),
                    "rating": p.get("rating", 0),
                    "queue_risk": p.get("queue_risk", "低"),
                    "avg_price_per_person": p.get("avg_price_per_person", 0),
                }
                for i, p in enumerate(route)
            ],
            ensure_ascii=False,
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"当前路线：\n{route_summary}\n\n用户说：{user_input}",
            },
        ]

        refine_meta = call_llm(messages, parse_json=True)

        replace_order = int(refine_meta.get("replace_order", 1))
        replace_category = refine_meta.get("category", "餐饮")

        intent["_refine"] = refine_meta

        # Carry preference into POISearchNode so it applies sub_category ordering
        pref_subs = refine_meta.get("new_constraints", {}).get("prefer_sub_category", [])
        if pref_subs:
            if replace_category == "餐饮":
                intent["food_pref"] = pref_subs
            else:
                intent["culture_pref"] = pref_subs

        # Extract geographic + budget context from the existing route POIs
        # so POISearchNode can search in the right area
        cities = [p.get("city", "") for p in route if p.get("city")]
        areas  = [p.get("area", "") for p in route if p.get("area")]
        city = cities[0] if cities else ""
        area = areas[0] if areas else ""

        # Estimate budget from the route's average price
        prices = [p.get("avg_price_per_person", 0) for p in route if p.get("avg_price_per_person")]
        budget_pp = int(max(prices) * 1.5) if prices else 9999

        intent["city"] = city
        intent["area"] = area
        intent["budget_per_person"] = budget_pp
        # POISearchNode uses must_include_categories to know what to search
        intent["must_include_categories"] = [replace_category]

        # locked_nodes: 0-based indices of POIs that should NOT be replaced
        locked_nodes = [
            i for i, p in enumerate(route)
            if p.get("order", i + 1) != replace_order
        ]

        updates = list(state.get("stream_updates", []))
        poi_name = next(
            (p.get("name", "") for p in route if p.get("order", 0) == replace_order),
            f"第{replace_order}个地点",
        )
        lang = state.get("language", "zh-TW")
        import route_planner.i18n as _i18n
        updates.append(_i18n.step("refine_start", lang,
            name=poi_name,
            city=_i18n.translate_field("city", city, lang),
            area=_i18n.translate_field("area", area, lang),
            cat=_i18n.translate_field("category", replace_category, lang)))

        return {
            **state,
            "intent": intent,
            "locked_nodes": locked_nodes,
            "stream_updates": updates,
        }
