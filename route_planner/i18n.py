"""Minimal i18n for user-facing output strings."""
import math as _math

# ---------------------------------------------------------------------------
# Translation tables
# ---------------------------------------------------------------------------

_QUEUE = {
    "zh-CN": {
        "high": "晚高峰等位约{peak}分钟，建议提前到店",
        "mid":  "高峰期等位约{peak}分钟",
        "low":  "基本无需等位",
    },
    "zh-TW": {
        "high": "晚高峰等位約{peak}分鐘，建議提前到店",
        "mid":  "高峰期等位約{peak}分鐘",
        "low":  "基本無需等位",
    },
    "en": {
        "high": "Peak hours wait ~{peak} min, arrive early",
        "mid":  "Busy periods wait ~{peak} min",
        "low":  "Usually no queue",
    },
}

_TRANSPORT = {
    "zh-CN": {
        "walk": "步行约{n}分钟",
        "ride": "骑行/打车约{n}分钟",
        "taxi": "打车约{n}分钟",
    },
    "zh-TW": {
        "walk": "步行約{n}分鐘",
        "ride": "騎車/的士約{n}分鐘",
        "taxi": "的士約{n}分鐘",
    },
    "en": {
        "walk": "Walk ~{n} min",
        "ride": "Ride/Taxi ~{n} min",
        "taxi": "Taxi ~{n} min",
    },
}

_TIME = {
    "zh-CN": ("{h}小时{m}分钟", "{h}小时"),
    "zh-TW": ("{h}小時{m}分鐘", "{h}小時"),
    "en":    ("{h}h {m}min",    "{h}h"),
}

_SUMMARY = {
    "zh-CN": "为你安排了{n}站行程，预计游玩{t}{deals}，餐饮消费约{b}元。",
    "zh-TW": "為你安排了{n}站行程，預計遊玩{t}{deals}，餐飲消費約{b}元。",
    "en":    "Planned {n} stops, est. {t}{deals}, dining ~HKD {b}.",
}

_DEALS = {
    "zh-CN": "，{n}处有团购优惠",
    "zh-TW": "，{n}處有團購優惠",
    "en":    ", {n} with group deals",
}

_FULFILLMENT = {
    "zh-CN": {
        "dining_ok":       "餐饮安排 ✓ （{n}个）",
        "dining_mismatch": "餐饮不足：要求{req}个，实际安排{got}个",
        "dining_tip":      "可说「再加一家餐厅」进行调整",
        "food_ok":         "餐饮偏好 {pref} ✓ （{names}）",
        "food_miss":       "未找到 {pref} 餐厅，以 {sub}（{names}）替代",
        "food_tip":        "该商圈暂无 {cuisine}；可说「换一家 {cuisine} 餐厅」",
        "culture_ok":      "文化偏好 {pref} ✓ （{names}）",
        "culture_miss":    "未找到 {pref} 类地点，以 {sub}（{names}）替代",
        "culture_tip":     "可说「换一个 {pref}」",
        "avoid_violated":  "包含了你想避开的类型（{avoid}）：{names}",
        "avoid_tip":       "可说「去掉 {names}」进行替换",
    },
    "zh-TW": {
        "dining_ok":       "餐飲安排 ✓ （{n}個）",
        "dining_mismatch": "餐飲不足：要求{req}個，實際安排{got}個",
        "dining_tip":      "可說「再加一家餐廳」進行調整",
        "food_ok":         "餐飲偏好 {pref} ✓ （{names}）",
        "food_miss":       "未找到 {pref} 餐廳，以 {sub}（{names}）替代",
        "food_tip":        "此商圈暫無 {cuisine}；可說「換一家 {cuisine} 餐廳」",
        "culture_ok":      "文化偏好 {pref} ✓ （{names}）",
        "culture_miss":    "未找到 {pref} 類地點，以 {sub}（{names}）替代",
        "culture_tip":     "可說「換一個 {pref}」",
        "avoid_violated":  "包含了你想避開的類型（{avoid}）：{names}",
        "avoid_tip":       "可說「去掉 {names}」進行替換",
    },
    "en": {
        "dining_ok":       "Dining ✓ ({n} stop(s))",
        "dining_mismatch": "Dining mismatch: requested {req}, got {got}",
        "dining_tip":      "Say 'add another restaurant' to adjust",
        "food_ok":         "{pref} ✓ ({names})",
        "food_miss":       "No {pref} found, substituted with {sub} ({names})",
        "food_tip":        "No {cuisine} in this area; say 'swap a {cuisine} restaurant'",
        "culture_ok":      "{pref} ✓ ({names})",
        "culture_miss":    "No {pref} found, substituted with {sub} ({names})",
        "culture_tip":     "Say 'swap for a {pref}'",
        "avoid_violated":  "Includes types you wanted to avoid ({avoid}): {names}",
        "avoid_tip":       "Say 'remove {names}' to swap",
    },
}

# Language name shown inside LLM prompts
LANG_NAME = {
    "zh-CN": "简体中文",
    "zh-TW": "繁體中文",
    "en":    "English",
}

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def normalize(lang: str) -> str:
    """Map any language tag to one of our three supported keys."""
    if lang.startswith("zh-TW") or lang.startswith("zh-HK"):
        return "zh-TW"
    if lang.startswith("zh"):
        return "zh-CN"
    if lang.startswith("en"):
        return "en"
    return "zh-TW"  # default for HK dataset


def queue_tip(poi: dict, lang: str = "zh-TW") -> str:
    risk = poi.get("queue_risk", "低")
    peak = poi.get("queue_minutes_peak", 0)
    t = _QUEUE[normalize(lang)]
    if risk == "高" and peak > 0:
        return t["high"].format(peak=peak)
    if risk == "中" and peak > 0:
        return t["mid"].format(peak=peak)
    return t["low"]


def transport_text(km: float, lang: str = "zh-TW") -> str:
    t = _TRANSPORT[normalize(lang)]
    if km <= 1.5:
        return t["walk"].format(n=max(5, round(km * 15)))
    if km <= 5.0:
        return t["ride"].format(n=max(8, round(km * 4)))
    return t["taxi"].format(n=max(15, round(km * 3)))


def time_str(total_mins: int, lang: str = "zh-TW") -> str:
    h, m = divmod(total_mins, 60)
    with_m, no_m = _TIME[normalize(lang)]
    return with_m.format(h=h, m=m) if m else no_m.format(h=h)


def summary(n_stops: int, total_mins: int, dining_budget: int, deals_count: int, lang: str = "zh-TW") -> str:
    t = _SUMMARY[normalize(lang)]
    deals = _DEALS[normalize(lang)].format(n=deals_count) if deals_count else ""
    return t.format(n=n_stops, t=time_str(total_mins, lang), b=dining_budget, deals=deals)


def f(key: str, lang: str = "zh-TW", **kwargs) -> str:
    """Look up a fulfillment template and format it."""
    return _FULFILLMENT[normalize(lang)][key].format(**kwargs)


# ---------------------------------------------------------------------------
# Field-level translations (sub_category, category, trend_tag, queue_risk)
# ---------------------------------------------------------------------------

_SUB_CATEGORY_EN: dict[str, str] = {
    # Chinese / HK style
    "港式": "Hong Kong Style", "茶餐廳": "Cha Chaan Teng", "廣東菜": "Cantonese",
    "點心": "Dim Sum", "潮州菜": "Chiu Chow", "上海菜": "Shanghainese",
    "川菜": "Sichuan", "湖南菜": "Hunan", "客家菜": "Hakka",
    "北京菜": "Peking", "東北菜": "Northeastern", "江浙菜": "Jiang-Zhe",
    "淮揚菜": "Huaiyang", "順德菜": "Shunde", "廣西菜": "Guangxi",
    "貴州菜": "Guizhou", "湖北菜": "Hubei", "新疆菜": "Xinjiang",
    "山西菜": "Shanxi", "農家菜": "Village Food", "京川滬菜": "Jing-Chuan-Hu",
    "台灣菜": "Taiwanese",
    # Japanese
    "日本料理": "Japanese", "壽司": "Sushi", "拉麵": "Ramen", "居酒屋": "Izakaya",
    # Korean / Southeast Asian
    "韓國料理": "Korean", "泰國料理": "Thai", "越南菜": "Vietnamese",
    "印度菜": "Indian", "印尼菜": "Indonesian", "馬來西亞菜": "Malaysian",
    "新加坡菜": "Singaporean", "緬甸菜": "Burmese", "菲律賓菜": "Filipino",
    "中東菜": "Middle Eastern", "黎巴嫩菜": "Lebanese", "土耳其菜": "Turkish",
    "尼泊爾菜": "Nepalese",
    # Western
    "西餐": "Western", "法國菜": "French", "意大利菜": "Italian",
    "西班牙菜": "Spanish", "地中海菜": "Mediterranean", "扒房": "Steakhouse",
    "美式餐廳": "American", "國際料理": "International", "融合料理": "Fusion",
    "英式料理": "British", "葡式料理": "Portuguese", "德國菜": "German",
    "瑞士菜": "Swiss", "愛爾蘭菜": "Irish", "荷蘭菜": "Dutch",
    "比利時菜": "Belgian", "東歐菜": "Eastern European",
    "墨西哥菜": "Mexican", "阿根廷菜": "Argentinian", "秘魯菜": "Peruvian",
    "非洲菜": "African", "埃及菜": "Egyptian",
    # Dining styles
    "火鍋": "Hot Pot", "燒烤": "BBQ", "海鮮": "Seafood",
    "素食": "Vegetarian", "自助餐": "Buffet", "早午餐": "Brunch",
    "麵食": "Noodles",
    # Casual
    "咖啡店": "Café", "甜品": "Dessert", "麵包店": "Bakery",
    "快餐": "Fast Food", "酒吧": "Bar",
}

_CATEGORY_EN: dict[str, str] = {
    "餐饮": "Dining", "文化": "Culture", "娱乐": "Entertainment",
    "自然": "Nature", "购物": "Shopping",
}

_CATEGORY_TW: dict[str, str] = {
    "餐饮": "餐飲", "娱乐": "娛樂", "购物": "購物",
    # 文化 / 自然 are the same in both
}

_LOCATION_EN: dict[str, str] = {
    # Cities — both Traditional and Simplified variants
    "香港": "Hong Kong", "上海": "Shanghai", "北京": "Beijing",
    "廣州": "Guangzhou", "广州": "Guangzhou",
    "深圳": "Shenzhen", "成都": "Chengdu",
    # HK districts — Traditional
    "旺角": "Mong Kok", "中環": "Central", "銅鑼灣": "Causeway Bay",
    "尖沙咀": "Tsim Sha Tsui", "灣仔": "Wan Chai", "九龍": "Kowloon",
    "荃灣": "Tsuen Wan", "屯門": "Tuen Mun", "元朗": "Yuen Long",
    "沙田": "Sha Tin", "大埔": "Tai Po", "將軍澳": "Tseung Kwan O",
    "西環": "Sai Wan", "上環": "Sheung Wan", "北角": "North Point",
    "觀塘": "Kwun Tong", "深水埗": "Sham Shui Po", "黃大仙": "Wong Tai Sin",
    "太古": "Taikoo", "西九龍": "West Kowloon", "港島": "Hong Kong Island",
    "新界": "New Territories", "九龍城": "Kowloon City", "油麻地": "Yau Ma Tei",
    "佐敦": "Jordan", "紅磡": "Hung Hom", "葵涌": "Kwai Chung",
    "青衣": "Tsing Yi", "馬鞍山": "Ma On Shan", "天水圍": "Tin Shui Wai",
    # HK districts — Simplified variants (fallback if LLM outputs Simplified)
    "中环": "Central", "铜锣湾": "Causeway Bay", "湾仔": "Wan Chai",
    "九龙": "Kowloon", "荃湾": "Tsuen Wan", "屯门": "Tuen Mun",
    "将军澳": "Tseung Kwan O", "西环": "Sai Wan", "上环": "Sheung Wan",
    "观塘": "Kwun Tong", "黄大仙": "Wong Tai Sin", "西九龙": "West Kowloon",
    "港岛": "Hong Kong Island", "九龙城": "Kowloon City", "红磡": "Hung Hom",
    "马鞍山": "Ma On Shan", "天水围": "Tin Shui Wai",
    # Mainland landmarks
    "外灘": "The Bund", "外滩": "The Bund", "三里屯": "Sanlitun",
}


_TREND_EN: dict[str, str] = {
    "火爆": "Trending", "新晋": "Rising", "经典": "Classic",
    "新晉": "Rising", "經典": "Classic",  # Traditional Chinese variants
}

_QUEUE_RISK_EN: dict[str, str] = {"高": "High", "中": "Medium", "低": "Low"}


# ---------------------------------------------------------------------------
# Step messages (SSE progress updates)
# ---------------------------------------------------------------------------

_STEPS = {
    "zh-CN": {
        "intent_done":    "已解析需求：{city}{area}，{time}（{dur}小时），{party}人，预算{budget}元，{cats}{dining}",
        "dining_note":    "，{n}个餐饮活动",
        "poi_found":      "找到候选POI：{summary}",
        "geo_done":       "地理聚合完成：中心半径{r}km，时间预算{dur}小时→参考{n}站",
        "route_ok":       "✅ 路线自检通过",
        "route_warn":     "⚠️ 自检发现问题：{reason}，正在重新规划…",
        "route_done":     "路线生成完成，共{n}个地点",
        "enrich_done":    "已补充团购/排队/趋势信息",
        "output_done":    "路线规划完成，已生成地图链接",
        "refine_start":   "正在替换：{name}（搜索 {city}{area} 的{cat}候选）",
        "cache_hit":      "缓存命中，直接返回结果",
        "intent_cache":   "意图缓存命中，直接返回结果",
    },
    "zh-TW": {
        "intent_done":    "已解析需求：{city}{area}，{time}（{dur}小時），{party}人，預算{budget}元，{cats}{dining}",
        "dining_note":    "，{n}個餐飲活動",
        "poi_found":      "找到候選POI：{summary}",
        "geo_done":       "地理聚合完成：中心半徑{r}km，時間預算{dur}小時→參考{n}站",
        "route_ok":       "✅ 路線自檢通過",
        "route_warn":     "⚠️ 自檢發現問題：{reason}，正在重新規劃…",
        "route_done":     "路線規劃完成，共{n}個地點",
        "enrich_done":    "已補充排隊/趨勢資訊",
        "output_done":    "路線規劃完成，已生成地圖連結",
        "refine_start":   "正在替換：{name}（搜索 {city}{area} 的{cat}候選）",
        "cache_hit":      "快取命中，直接返回結果",
        "intent_cache":   "意圖快取命中，直接返回結果",
    },
    "en": {
        "intent_done":    "Parsed: {city} {area} | {time} ({dur}h) | {party} pax | budget HKD {budget} | {cats}{dining}",
        "dining_note":    ", {n} meal occasion(s)",
        "poi_found":      "Found candidates: {summary}",
        "geo_done":       "Geo-cluster: radius {r}km, {dur}h → up to {n} stops",
        "route_ok":       "✅ Route validated",
        "route_warn":     "⚠️ Validation issue: {reason} — retrying…",
        "route_done":     "Route ready: {n} stop(s)",
        "enrich_done":    "Queue / trend info added",
        "output_done":    "Route complete, map generated",
        "refine_start":   "Replacing: {name} (searching {cat} in {city} {area})",
        "cache_hit":      "Cache hit — returning cached result",
        "intent_cache":   "Intent cache hit — returning cached result",
    },
}


def step(key: str, lang: str = "zh-TW", **kwargs) -> str:
    """Return a localized step message."""
    tpl = _STEPS[normalize(lang)].get(key, key)
    return tpl.format(**kwargs) if kwargs else tpl


# ---------------------------------------------------------------------------
# Simplified ↔ Traditional conversion via OpenCC
# ---------------------------------------------------------------------------
import opencc as _opencc
_s2t = _opencc.OpenCC("s2t")
_t2s = _opencc.OpenCC("t2s")


def to_traditional(text: str) -> str:
    return _s2t.convert(text)


def to_simplified(text: str) -> str:
    return _t2s.convert(text)


def translate_field(field: str, value: str, lang: str = "zh-TW") -> str:
    """Translate a POI field value to the target language."""
    lang_key = normalize(lang)
    # zh-TW: convert Simplified-only system words to Traditional
    if lang_key == "zh-TW":
        if field == "category":
            return _CATEGORY_TW.get(value, value)
        return value
    if lang_key == "zh-CN":
        if field == "sub_category":
            tags = [to_simplified(t.strip()) for t in value.split("、") if t.strip()]
            return "、".join(tags) if tags else value
        if field in ("city", "area"):
            return to_simplified(value)
        if field in ("category", "trend_tag", "queue_risk"):
            return to_simplified(value)
        return value
    if lang_key != "en":
        return value
    if field == "sub_category":
        # May be compound like "日本料理、壽司" — translate each tag
        tags = [_SUB_CATEGORY_EN.get(t.strip(), t.strip()) for t in value.split("、") if t.strip()]
        return " / ".join(tags) if tags else value
    if field == "category":
        return _CATEGORY_EN.get(value, value)
    if field == "trend_tag":
        # trend_tag may have suffix like "火爆（已售1.2万单）"
        import re as _re
        m = _re.match(r"([^\（(]+)[（(]已售([\d.]+)(万?)单[）)]", value)
        for zh, en in _TREND_EN.items():
            if value.startswith(zh):
                if m:
                    n = m.group(2)
                    unit = "0k+" if m.group(3) else "+"
                    return f"{en} ({n}{unit} sold)"
                return en
        return value
    if field == "queue_risk":
        return _QUEUE_RISK_EN.get(value, value)
    if field in ("city", "area"):
        return _LOCATION_EN.get(value, value)
    return value
