"""
IntentAgent: parse natural-language user input into structured JSON intent.
"""
import json
import re
from typing import Dict, Any, Tuple

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.llm import call_llm
import route_planner.i18n as i18n
from route_planner.i18n import normalize as _norm, LANG_NAME


def _parse_cot_response(raw: str) -> Tuple[str, dict]:
    """Extract reasoning text and JSON from CoT response."""
    raw = raw.strip()
    json_match = re.search(r"(\{[\s\S]+\})", raw)
    if not json_match:
        return "", json.loads(raw)
    json_str = json_match.group(1)
    intent = json.loads(json_str)
    reasoning = raw[:json_match.start()].strip()
    # Strip "思考：" or "Reasoning:" prefix
    reasoning = re.sub(r"^(思考|Reasoning)[：:]\s*", "", reasoning).strip()
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

    # Ensure new fields have defaults
    if "prefer_local" not in intent:
        intent["prefer_local"] = False
    if "scenarios" not in intent:
        intent["scenarios"] = []

    return intent

_SYSTEM_PROMPT_TEMPLATE = """\
__LANG_INSTRUCTION__

你是一个本地路线规划助手的意图解析模块。
用户会用自然语言描述出行需求，你需要先简要说明推理过程，再输出结构化 JSON。

__COT_FORMAT__

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
  "dining_count": 整数（用户明确提到的餐饮活动数量，未提到则为0）,
  "prefer_local": false（布尔值，用户提到"地道/本地/老字号/老铺/道地/authentic/local"等词时为true）,
  "scenarios": []（场合列表，从下列词中选：情侶約會/朋友聚餐/家庭親子/慶生/商務接待/一人食/打卡拍照；用户未明确提及时为空数组）
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

prefer_local 判断规则：
- "地道" / "本地" / "本土" / "老字号" / "老铺" / "道地" / "old school" / "authentic" / "local" / "hidden gem" → true
- 其余情况（泛指美食/菜系偏好/无特别强调地道） → false

scenarios 词汇映射（用户说的 → 应输出的 scenarios 元素）：
- 约会/情侣/couple/romantic/两人世界/浪漫 → "情侶約會"
- 朋友/友聚/和朋友/friends gathering → "朋友聚餐"
- 家庭/家人/带孩子/亲子/儿童/family/kids/children → "家庭親子"
- 生日/过生日/庆生/birthday/anniversary/纪念日 → "慶生"
- 商务/工作/客户/接待/business/work → "商務接待"
- 一个人/独自/独食/alone/solo dining → "一人食"
- 打卡/拍照/instagrammable/check-in/网红/check in/photo spot → "打卡拍照"
- 带孩子/推车/长者/老人/无障碍/wheelchair/stroller/elderly → "家庭親子"（补充到已有家庭亲子规则）
"""


def _build_system_prompt(lang: str) -> str:
    lang_key = _norm(lang)
    if lang_key == "en":
        instruction = (
            "=== OUTPUT LANGUAGE: ENGLISH ONLY ===\n"
            "The user writes in English. Your ENTIRE response MUST be written in English. "
            "Using Chinese characters anywhere is FORBIDDEN."
        )
        cot_format = (
            "Output format (follow strictly, blank line between the two parts):\n"
            "Reasoning: [1-2 sentences in English summarising the user's needs as you understand them, "
            "e.g. \"The user wants to spend 3 hours near the waterfront, have Japanese food, budget HKD 400.\" "
            "Do NOT mention any technical field names.]"
        )
    elif lang_key == "zh-CN":
        instruction = (
            "=== 输出语言：仅限简体中文 ===\n"
            "用户使用简体中文输入。你的全部输出必须使用简体中文，"
            "严禁出现任何繁体字（如：來應該寫成来，為應該寫成为）。"
        )
        cot_format = (
            "输出格式（严格遵守，两部分之间空一行）：\n"
            "思考：[1-2句简体中文，像向朋友复述一样描述你理解的用户需求，"
            "例如：「用户想在外滩逛3小时，吃本帮菜、看历史建筑，预算300元。」严禁出现任何技术字段名]"
        )
    else:
        instruction = (
            "=== 輸出語言：僅限繁體中文 ===\n"
            "用戶使用繁體中文輸入。你的全部輸出必須使用繁體中文，"
            "嚴禁出現任何簡體字（如：来應寫成來，为應寫成為，时應寫成時）。"
        )
        cot_format = (
            "輸出格式（嚴格遵守，兩部分之間空一行）：\n"
            "思考：[1-2句繁體中文，像向朋友複述一樣描述你理解的用戶需求，"
            "例如：「用戶想在外灘逛3小時，吃本幫菜、看歷史建築，預算300元。」嚴禁出現任何技術字段名]"
        )
    return (
        _SYSTEM_PROMPT_TEMPLATE
        .replace("__LANG_INSTRUCTION__", instruction)
        .replace("__COT_FORMAT__", cot_format)
    )


class IntentNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        user_input = state["user_input"]
        lang = state.get("language", "zh-TW")
        history = state.get("conversation_history", [])

        lang_key = _norm(lang)
        _user_prefix = {
            "en":    "[Reply in English only] ",
            "zh-CN": "[请用简体中文回复] ",
            "zh-TW": "[請用繁體中文回覆，嚴禁使用簡體字] ",
        }.get(lang_key, "")

        messages = [{"role": "system", "content": _build_system_prompt(lang)}]
        for turn in history:
            messages.append(turn)
        messages.append({"role": "user", "content": _user_prefix + user_input})

        raw = call_llm(messages, parse_json=False)

        # Split reasoning and JSON
        reasoning, intent = _parse_cot_response(raw)

        # Code-level validation and auto-fix
        intent = _validate_and_fix(intent)

        updates = list(state.get("stream_updates", []))
        if reasoning:
            updates.append(f"💡 {reasoning}")

        city = i18n.translate_field("city", intent.get("city", ""), lang)
        area = i18n.translate_field("area", intent.get("area", ""), lang)
        budget = intent.get("budget_total", "")
        duration = intent.get("duration_hours", "")
        party = intent.get("party_size", 2)
        tr = intent.get("time_range", {})
        time_str = f"{tr.get('start','')}-{tr.get('end','')}" if tr else ""
        cats_list = intent.get("must_include_categories", [])
        sep = " / " if i18n.normalize(lang) == "en" else "、"
        cats = sep.join(i18n.translate_field("category", c, lang) for c in cats_list)
        dining_count = intent.get("dining_count", 0)
        dining_note = i18n.step("dining_note", lang, n=dining_count) if dining_count > 0 else ""
        updates.append(i18n.step("intent_done", lang,
            city=city, area=area, time=time_str, dur=duration,
            party=party, budget=budget, cats=cats, dining=dining_note))

        return {**state, "intent": intent, "stream_updates": updates}
