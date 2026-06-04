"""
IntentAgent: parse natural-language user input into structured JSON intent.
"""
import json
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.llm import call_llm

_SYSTEM_PROMPT = """\
你是一个本地路线规划助手的意图解析模块。
用户会用自然语言描述出行需求，你需要将其解析为标准的结构化 JSON。

输出必须严格遵守以下 JSON Schema，直接输出 JSON，不要有任何额外文字：
{
  "city": "城市名（字符串）",
  "area": "商圈/区域（字符串）",
  "date": "日期描述，如'今天'/'周末'/'明天'（字符串）",
  "time_range": {"start": "HH:MM", "end": "HH:MM"},
  "duration_hours": 整数（行程总小时数）,
  "budget_total": 整数（总预算，元），
  "budget_per_person": 整数（人均预算，元），
  "party_size": 整数（出行人数，默认2）,
  "food_pref": ["菜系偏好列表"],
  "culture_pref": ["文化偏好列表，如历史建筑/博物馆/艺术"],
  "avoid": ["要避开的类型"],
  "must_include_categories": ["必须包含的POI类别，从餐饮/文化/娱乐/购物/自然中选"]
}

规则：
- budget_per_person = budget_total / party_size（四舍五入到整数）
- 若用户未指定时间，time_range默认为 {"start": "14:00", "end": "21:00"}
- duration_hours = time_range end 与 start 的差值（小时），若无法计算默认为 4
- 若用户未指定人数，party_size默认为 2
- 若用户未提到预算，budget_total默认为 200
- must_include_categories 必须至少包含一项
- 若 duration_hours >= 5 或用户提到"一整天/全天/一天"，must_include_categories 必须同时包含"餐饮"和至少一项"文化"或"娱乐"，不能只有餐饮
"""


class IntentNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        user_input = state["user_input"]
        history = state.get("conversation_history", [])

        messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
        for turn in history:
            messages.append(turn)
        messages.append({"role": "user", "content": user_input})

        intent = call_llm(messages, parse_json=True)

        # Ensure duration_hours is always populated
        if not intent.get("duration_hours"):
            try:
                tr = intent.get("time_range", {})
                sh, sm = map(int, tr["start"].split(":"))
                eh, em = map(int, tr["end"].split(":"))
                intent["duration_hours"] = max(1, round((eh * 60 + em - sh * 60 - sm) / 60))
            except Exception:
                intent["duration_hours"] = 4

        updates = list(state.get("stream_updates", []))
        city = intent.get("city", "")
        area = intent.get("area", "")
        budget = intent.get("budget_total", "")
        cats = "、".join(intent.get("must_include_categories", []))
        updates.append(f"已解析需求：{city}{area}，预算{budget}元，{cats}")

        return {**state, "intent": intent, "stream_updates": updates}
