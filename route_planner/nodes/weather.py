"""WeatherNode: fetch real-time weather from Gaode API and inject routing hints."""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
import route_planner.i18n as i18n

# Gaode adcode table (city name → 6-digit adcode)
_ADCODE: dict[str, str] = {
    "上海": "310000", "北京": "110000", "广州": "440100", "廣州": "440100",
    "深圳": "440300", "杭州": "330100", "成都": "510100", "重庆": "500000",
    "重慶": "500000", "南京": "320100", "武汉": "420100", "武漢": "420100",
    "西安": "610100", "苏州": "320500", "蘇州": "320500",
    "厦门": "350200", "廈門": "350200", "青岛": "370200", "青島": "370200",
    "天津": "120000", "沈阳": "210100", "瀋陽": "210100",
    "长沙": "430100", "長沙": "430100", "郑州": "410100", "鄭州": "410100",
    "合肥": "340100", "昆明": "530100", "南宁": "450100", "南寧": "450100",
    "香港": "810000",
}

# Weather conditions that indicate rain
_RAIN_WORDS = {"阵雨", "小雨", "中雨", "大雨", "暴雨", "特大暴雨", "雷阵雨", "雷陣雨",
               "雨夹雪", "雨夾雪", "冻雨", "凍雨", "毛毛雨", "细雨", "阵雪夹雨"}


def _parse_target_date(date_str: str) -> str:
    """Convert '今天'/'明天'/'周末' etc. to YYYY-MM-DD."""
    today = datetime.now()
    d = date_str.lower().strip()
    if any(w in d for w in ("今天", "今日", "today", "now", "现在", "現在")):
        return today.strftime("%Y-%m-%d")
    if any(w in d for w in ("明天", "明日", "tomorrow")):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if "后天" in d or "後天" in d:
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    if any(w in d for w in ("周末", "週末", "weekend", "saturday", "sunday", "周六", "週六", "周日", "週日")):
        days_ahead = (5 - today.weekday()) % 7  # next Saturday
        if days_ahead == 0:
            days_ahead = 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    # Fallback: today
    return today.strftime("%Y-%m-%d")


def _fetch_forecast(city: str, api_key: str) -> list[dict]:
    """Call Gaode weather forecast API; return list of daily cast dicts."""
    adcode = _ADCODE.get(city, city)
    params = urllib.parse.urlencode({
        "city": adcode,
        "key": api_key,
        "extensions": "all",
        "output": "json",
    })
    try:
        with urllib.request.urlopen(
            f"https://restapi.amap.com/v3/weather/weatherInfo?{params}", timeout=5
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    if data.get("status") != "1" or not data.get("forecasts"):
        return []
    return data["forecasts"][0].get("casts", [])


def _pick_cast(casts: list[dict], target_date: str) -> dict | None:
    for c in casts:
        if c.get("date") == target_date:
            return c
    return casts[0] if casts else None


def _classify(cast: dict, time_range: dict | None) -> dict:
    """Interpret a daily forecast cast into routing-relevant signals."""
    start_h = 14
    if time_range:
        try:
            start_h = int(time_range.get("start", "14:00").split(":")[0])
        except Exception:
            pass

    if start_h < 18:
        weather_desc = cast.get("dayweather", "")
        temp = _safe_float(cast.get("daytemp", 25))
    else:
        weather_desc = cast.get("nightweather", "")
        temp = _safe_float(cast.get("nighttemp", 20))

    is_rainy  = weather_desc in _RAIN_WORDS or "雨" in weather_desc
    is_stormy = any(w in weather_desc for w in ("暴雨", "台风", "台風", "颱風", "龍捲"))
    is_hot    = temp >= 33
    is_cold   = temp <= 10

    if is_stormy:
        condition = "storm"
    elif is_rainy:
        condition = "rain"
    elif is_hot:
        condition = "hot"
    elif is_cold:
        condition = "cold"
    else:
        condition = "clear"

    prefer_indoor = condition in ("storm", "rain", "hot")

    return {
        "date": cast.get("date", ""),
        "weather": weather_desc,
        "temperature": temp,
        "condition": condition,
        "prefer_indoor": prefer_indoor,
        "is_rainy": is_rainy,
        "is_stormy": is_stormy,
        "is_hot": is_hot,
        "is_cold": is_cold,
    }


def _safe_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 20.0


class WeatherNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        intent = state.get("intent", {})
        city       = intent.get("city", "")
        date_str   = intent.get("date", "今天")
        time_range = intent.get("time_range")
        lang       = state.get("language", "zh-TW")
        api_key    = os.environ.get("AMAP_API_KEY", "")

        updates = list(state.get("stream_updates", []))

        if not api_key or not city:
            return {**state, "weather": {}}

        target_date = _parse_target_date(date_str)
        casts = _fetch_forecast(city, api_key)
        cast = _pick_cast(casts, target_date)

        if not cast:
            return {**state, "weather": {}}

        weather_info = _classify(cast, time_range)

        # Inject weather into intent so RouteNode can act on it
        updated_intent = dict(intent)
        updated_intent["weather"] = weather_info
        # prefer_indoor already in weather_info — expose at top level too
        updated_intent["prefer_indoor"] = weather_info["prefer_indoor"]

        updates.append(i18n.weather_step(weather_info, lang))

        return {
            **state,
            "intent": updated_intent,
            "weather": weather_info,
            "stream_updates": updates,
        }
