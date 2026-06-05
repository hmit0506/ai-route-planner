"""
IntentAgent: parse natural-language user input into structured JSON intent.
"""
import json
import re
from typing import Dict, Any, Tuple

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.llm import call_llm
from route_planner.i18n import normalize as _norm, LANG_NAME


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

    # Normalize must_include_categories to fixed Simplified Chinese keys (DB query values)
    _CAT_NORM = {
        "餐飲": "餐饮", "餐厅": "餐饮", "飲食": "餐饮", "饮食": "餐饮", "dining": "餐饮",
        "文化": "文化", "文化景點": "文化", "culture": "文化",
        "娛樂": "娱乐", "entertainment": "娱乐",
        "購物": "购物", "shopping": "购物",
        "自然": "自然", "nature": "自然",
    }
    cats = intent.get("must_include_categories") or []
    intent["must_include_categories"] = [_CAT_NORM.get(c, c) for c in cats]

    # Ensure must_include_categories is not empty
    if not intent.get("must_include_categories"):
        intent["must_include_categories"] = ["餐饮"]

    # Note: do NOT auto-add "文化" here — database may not have cultural POIs.
    # LLM should include it in must_include_categories only when user explicitly asks.

    # Ensure dining_count is a non-negative integer
    try:
        intent["dining_count"] = max(0, int(intent.get("dining_count", 0) or 0))
    except (TypeError, ValueError):
        intent["dining_count"] = 0

    # Sanity check: a trip >= 6 hours with dining_count=1 almost certainly means
    # the user didn't explicitly specify a count — reset to 0 so RouteAgent decides freely
    if intent["dining_count"] == 1 and intent.get("duration_hours", 0) >= 6:
        intent["dining_count"] = 0

    return intent

_SYSTEM_PROMPT_TEMPLATE = """\
你是一个本地路线规划助手的意图解析模块。
用户会用自然语言描述出行需求，你需要先简要说明推理过程，再输出结构化 JSON。

__LANG_INSTRUCTION__

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
  "dining_count": 整数（用户明确提到的餐饮活动数量，未提到则为0）
}

字段语言规则（重要）：
- city / area / food_pref / culture_pref / avoid 字段统一使用繁體中文，以便匹配資料庫（即使用户用英文或简体输入，也要转成繁体）
- must_include_categories 必须使用固定简体词汇：餐饮、文化、娱乐、购物、自然（这是内部查询key，不翻译）
- 推理说明（思考：那行）用用户实际使用的语言输出

food_pref 词汇对齐——数据库中实际存在的 sub_category 标准词（必须用这些词，不要用同义词）：
主要类别：港式、茶餐廳、廣東菜、點心、潮州菜、上海菜、川菜、湖南菜、客家菜、北京菜、東北菜
日式：日本料理、壽司、拉麵、居酒屋
西式：西餐、法國菜、意大利菜、西班牙菜、地中海菜、扒房、美式餐廳、英式料理、葡式料理
亚洲其他：韓國料理、泰國料理、越南菜、印度菜、印尼菜、馬來西亞菜、新加坡菜、中東菜
其他：火鍋、燒烤、海鮮、素食、自助餐、早午餐、國際料理、台灣菜、麵食
休閒：咖啡店、甜品、麵包店、快餐、酒吧

词汇映射规则（用户说的 → 应输出的 food_pref）：
- 壽司/刺身 → "壽司"
- 拉麵/烏冬/蕎麥麵/日式麵 → "拉麵" 或 "麵食"
- 居酒屋/串燒/日式小酒館 → "居酒屋"
- 日料/日式料理（泛指）→ "日本料理"
- 下午茶/奶茶/港式奶茶 → "港式" 或 "茶餐廳"
- 飲茶/點心/蝦餃燒賣 → "點心"
- 燒臘/叉燒/白切雞/煲仔飯 → "廣東菜"
- 小籠包/滬菜/上海菜 → "上海菜"
- 麻辣/四川/辣鍋 → "川菜"
- 打邊爐/涮鍋 → "火鍋"
- 牛排/扒 → "扒房" 或 "西餐"
- 咖啡/café/下午咖啡 → "咖啡店"
- 糖水/甜品/布甸/蛋糕 → "甜品"
- 麵包/三文治/輕食 → "麵包店"
- 越南粉/河粉/pho → "越南菜"
- 冬陰功/泰式 → "泰國料理"
如用户表达不在以上列表，用最接近的標準詞；若完全無对应，直接原词输出。

规则：
- budget_per_person = budget_total / party_size（四舍五入到整数）
- 若用户未指定时间，time_range默认为 {"start": "14:00", "end": "21:00"}
- duration_hours = time_range end 与 start 的差值（小时），若无法计算默认为 4
- 若用户未指定人数，party_size默认为 2
- 若用户未提到预算，budget_total默认为 200
- must_include_categories 必须至少包含一项
- 若 duration_hours >= 5 或用户提到"一整天/全天/一天"，must_include_categories 必须同时包含"餐饮"和至少一项"文化"或"娱乐"，不能只有餐饮
- dining_count = 用户明确提到了几个餐饮活动（注意：菜系偏好和泛指均不算次数）：
  "包括午饭和晚饭" → 2
  "喝下午茶，吃川菜" → 2（每个独立的饮食活动算1个）
  "吃顿好的" → 1
  "想吃日本料理" → 0（仅菜系偏好，未指定次数）
  "food and drinks" → 0（泛指饮食，未指定次数）
  "something to eat" → 0（泛指，未指定次数）
  "随便逛逛吃吃" → 0（未指定次数）
  "想吃日料，再喝杯咖啡" → 2（两个独立饮食活动）
"""


def _build_system_prompt(lang: str) -> str:
    lang_key = _norm(lang)
    if lang_key == "en":
        instruction = "The user is writing in English. Output the reasoning line in English too."
    elif lang_key == "zh-CN":
        instruction = "用户使用简体中文。推理说明用简体中文输出。"
    else:
        instruction = "用戶使用繁體中文。推理說明用繁體中文輸出。"
    return _SYSTEM_PROMPT_TEMPLATE.replace("__LANG_INSTRUCTION__", instruction)


class IntentNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        user_input = state["user_input"]
        lang = state.get("language", "zh-TW")
        history = state.get("conversation_history", [])

        messages = [{"role": "system", "content": _build_system_prompt(lang)}]
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
        dining_count = intent.get("dining_count", 0)
        dining_note = f"，{dining_count}个餐饮活动" if dining_count > 0 else ""
        updates.append(f"已解析需求：{city}{area}，{time_str}（{duration}小时），{party}人，预算{budget}元，{cats}{dining_note}")

        return {**state, "intent": intent, "stream_updates": updates}
