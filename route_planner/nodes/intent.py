"""
IntentAgent: parse natural-language user input into structured JSON intent.
"""
import json
import re
from typing import Dict, Any, Tuple

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.llm import call_llm


def _parse_cot_response(raw: str) -> Tuple[str, dict]:
    """Extract reasoning text and JSON from CoT response."""
    raw = raw.strip()
    # Find the JSON block (starts with { or ```json)
    json_match = re.search(r"(\{[\s\S]+\})", raw)
    if not json_match:
        return "", json.loads(raw)
    json_str = json_match.group(1)
    intent = json.loads(json_str)
    # Reasoning is everything before the JSON block
    reasoning = raw[:json_match.start()].strip()
    # Strip "思考：" prefix if present
    reasoning = re.sub(r"^思考[：:]\s*", "", reasoning).strip()
    return reasoning, intent


def _validate_and_fix(intent: dict) -> dict:
    """Auto-fix common IntentAgent errors."""
    # Fix duration_hours if missing or zero
    if not intent.get("duration_hours"):
        try:
            tr = intent.get("time_range", {})
            sh, sm = map(int, tr["start"].split(":"))
            eh, em = map(int, tr["end"].split(":"))
            intent["duration_hours"] = max(1, round((eh * 60 + em - sh * 60 - sm) / 60))
        except Exception:
            intent["duration_hours"] = 4

    # Fix budget_per_person if inconsistent with budget_total / party_size
    total = intent.get("budget_total", 0)
    party = intent.get("party_size", 2) or 2
    if total and party:
        expected_pp = round(total / party)
        if abs(intent.get("budget_per_person", 0) - expected_pp) > 5:
            intent["budget_per_person"] = expected_pp

    # Ensure must_include_categories is not empty
    if not intent.get("must_include_categories"):
        intent["must_include_categories"] = ["餐饮"]

    # Auto-add culture/entertainment for long trips
    cats = intent["must_include_categories"]
    if intent.get("duration_hours", 0) >= 5 and "餐饮" in cats:
        if not any(c in cats for c in ["文化", "娱乐"]):
            intent["must_include_categories"] = cats + ["文化"]

    # Ensure meal_plan is a list
    if not isinstance(intent.get("meal_plan"), list):
        intent["meal_plan"] = []

    return intent

_SYSTEM_PROMPT = """\
你是一个本地路线规划助手的意图解析模块。
用户会用自然语言描述出行需求，你需要先简要说明推理过程，再输出结构化 JSON。

输出格式（严格遵守，两部分之间空一行）：
思考：[1-2句话，像向朋友复述一样描述你理解的用户需求，例如："用户想在外滩逛3小时，吃本帮菜、看历史建筑，预算300元。"严禁出现任何技术字段名（must_include_categories、duration_hours、food_pref等均不允许出现）]

{"city": ..., "area": ..., ...}

JSON Schema：
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
  "avoid": ["要避开的类型或子类别"],
  "must_include_categories": ["必须包含的POI类别，从餐饮/文化/娱乐/购物/自然中选"],
  "meal_plan": ["用户明确提到的餐饮需求，如早餐/午饭/下午茶/晚饭/夜宵，未提到则为空列表"]
}

规则：
- budget_per_person = budget_total / party_size（四舍五入到整数）
- 若用户未指定时间，time_range默认为 {"start": "14:00", "end": "21:00"}
- duration_hours = time_range end 与 start 的差值（小时），若无法计算默认为 4
- 若用户未指定人数，party_size默认为 2
- 若用户未提到预算，budget_total默认为 200
- must_include_categories 必须至少包含一项
- 若 duration_hours >= 5 或用户提到"一整天/全天/一天"，must_include_categories 必须同时包含"餐饮"和至少一项"文化"或"娱乐"，不能只有餐饮
- meal_plan 要提取用户明确提到的每一个餐饮活动，包括正餐和饮品：
  "包括午饭和晚饭"→["午饭","晚饭"]
  "喝下午茶，吃川菜"→["下午茶","正餐"]（吃川菜算一顿正餐，川菜偏好写入food_pref）
  "早中晚餐都要加咖啡"→["早餐","午饭","晚饭","咖啡"]
"""


class IntentNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        user_input = state["user_input"]
        history = state.get("conversation_history", [])

        messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
        for turn in history:
            messages.append(turn)
        messages.append({"role": "user", "content": user_input})

        raw = call_llm(messages, parse_json=False)

        # Split reasoning and JSON
        reasoning, intent = _parse_cot_response(raw)

        # Code-level validation and auto-fix
        intent = _validate_and_fix(intent)

        updates = list(state.get("stream_updates", []))
        if reasoning:
            updates.append(f"💡 {reasoning}")

        city = intent.get("city", "")
        area = intent.get("area", "")
        budget = intent.get("budget_total", "")
        duration = intent.get("duration_hours", "")
        party = intent.get("party_size", 2)
        tr = intent.get("time_range", {})
        time_str = f"{tr.get('start','')}-{tr.get('end','')}" if tr else ""
        cats = "、".join(intent.get("must_include_categories", []))
        updates.append(f"已解析需求：{city}{area}，{time_str}（{duration}小时），{party}人，预算{budget}元，{cats}")

        return {**state, "intent": intent, "stream_updates": updates}
