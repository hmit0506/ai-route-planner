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
