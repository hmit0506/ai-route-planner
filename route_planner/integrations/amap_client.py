import json
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None


# =========================
# Endpoints
# =========================
AMAP_POI_URL = "https://restapi.amap.com/v3/place/text"
AMAP_STATIC_MAP_URL = "https://restapi.amap.com/v3/staticmap"

AMAP_WALK_ROUTE_URL = "https://restapi.amap.com/v3/direction/walking"
AMAP_DRIVE_ROUTE_URL = "https://restapi.amap.com/v3/direction/driving"
AMAP_TRANSIT_ROUTE_URL = "https://restapi.amap.com/v3/direction/transit/integrated"
AMAP_BIKE_ROUTE_URL = "https://restapi.amap.com/v4/direction/bicycling"

BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BASE_DIR.parent
GENERATED_KEYWORDS_PATH = BASE_DIR / "data" / "category_keywords.generated.json"

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")


# =========================
# Schema
# =========================
SCHEMA_FIELDS = [
    "id",
    "name",
    "category",
    "sub_category",
    "address",
    "city",
    "area",
    "lat",
    "lng",
    "rating",
    "taste_rating",
    "decor_rating",
    "service_rating",
    "hygiene_rating",
    "value_rating",
    "review_count",
    "half_year_sales",
    "avg_price_per_person",
    "queue_risk",
    "queue_minutes_peak",
    "queue_minutes_offpeak",
    "has_group_buy",
    "group_buy_title",
    "group_buy_original_price",
    "group_buy_current_price",
    "business_hours",
    "trend_tag",
    "recommend_count",
]

SCHEMA_DEFAULTS: Dict[str, Any] = {
    "id": "",
    "name": "",
    "category": "",
    "sub_category": "",
    "address": "",
    "city": "",
    "area": "",
    "lat": 0.0,
    "lng": 0.0,
    "rating": 4.3,
    "taste_rating": 0.0,
    "decor_rating": 0.0,
    "service_rating": 0.0,
    "hygiene_rating": 0.0,
    "value_rating": 4.2,
    "review_count": 0,
    "half_year_sales": 0,
    "avg_price_per_person": 0,
    "queue_risk": "中",
    "queue_minutes_peak": 20,
    "queue_minutes_offpeak": 5,
    "has_group_buy": False,
    "group_buy_title": "",
    "group_buy_original_price": 0,
    "group_buy_current_price": 0,
    "business_hours": "",
    "trend_tag": "热门",
    "recommend_count": 0,
}

DEFAULT_CATEGORY_KEYWORDS: Dict[str, str] = {
    "餐饮": "餐饮 本帮菜 本帮点心 火锅 日料 西餐 咖啡 茶饮",
    "文化": "文化 博物馆 历史建筑 艺术展 非遗 公园 景点",
    "娱乐": "娱乐 商圈 购物 影院 剧场 休闲",
    "自然": "自然 公园 风景",
    "购物": "购物 商场 步行街",
}

CATEGORY_TO_CORE = {
    "自然": "文化",
    "购物": "娱乐",
}

CATEGORY_TYPE_CODES = {
    "餐饮": "050000",
    "文化": "110000|140000",
    "娱乐": "060000|080000|090000",
}

TYPE_INFER_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "餐饮": (
        "餐饮服务", "中餐厅", "外国餐厅", "快餐厅", "咖啡厅",
        "茶艺馆", "甜品店", "小吃", "火锅", "面包甜点",
    ),
    "文化": (
        "风景名胜", "博物馆", "美术馆", "展览馆", "科技馆",
        "图书馆", "纪念馆", "文物古迹", "公园", "历史",
    ),
    "娱乐": (
        "购物服务", "商场", "超市", "步行街", "电影院",
        "体育休闲", "休闲", "游乐", "KTV", "剧场",
    ),
}

ROUTE_MODE_ALIASES = {
    "walking": "walking", "walk": "walking", "步行": "walking",
    "driving": "driving", "drive": "driving", "car": "driving", "驾车": "driving", "开车": "driving",
    "bicycling": "bicycling", "cycling": "bicycling", "bike": "bicycling", "骑行": "bicycling", "自行车": "bicycling",
    "transit": "transit", "public_transport": "transit", "public-transport": "transit",
    "公交": "transit", "地铁": "transit", "公共交通": "transit",
}

ROUTE_MODE_STYLE: Dict[str, Dict[str, Any]] = {
    "walking": {"weight": 5, "color": "0x3366FF"},
    "driving": {"weight": 6, "color": "0xFF6A00"},
    "bicycling": {"weight": 5, "color": "0x00A86B"},
    "transit": {"weight": 5, "color": "0x7B61FF"},
}

RISK_RANK = {"低": 1, "中": 2, "高": 3}

_ROUTE_POLYLINE_CACHE: Dict[Tuple[str, str, str, str], List[str]] = {}
_SEGMENT_DURATION_CACHE: Dict[Tuple[str, str, str, str], int] = {}


# =========================
# Helpers
# =========================
def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return " ".join(str(x).strip() for x in v if str(x).strip())
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, ""):
            return default
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v in (None, ""):
            return default
        return int(float(v))
    except Exception:
        return default


def _parse_location(location: str) -> Tuple[float, float]:
    try:
        lng_str, lat_str = str(location).split(",")
        return _to_float(lat_str, 0.0), _to_float(lng_str, 0.0)
    except Exception:
        return 0.0, 0.0


def normalize_route_mode(mode: str) -> str:
    return ROUTE_MODE_ALIASES.get(_as_text(mode).lower(), "walking")


def _map_to_core_category(category: str) -> str:
    c = _as_text(category)
    return CATEGORY_TO_CORE.get(c, c)


def _load_category_keywords() -> Dict[str, str]:
    merged = dict(DEFAULT_CATEGORY_KEYWORDS)
    if not GENERATED_KEYWORDS_PATH.exists():
        return merged
    try:
        raw = json.loads(GENERATED_KEYWORDS_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for k, v in raw.items():
                key = _as_text(k)
                if not key:
                    continue
                if isinstance(v, list):
                    val = " ".join(_as_text(x) for x in v if _as_text(x))
                else:
                    val = _as_text(v)
                if val:
                    merged[key] = val
    except Exception:
        pass
    return merged


CATEGORY_KEYWORDS = _load_category_keywords()


def _get_key() -> str:
    key = os.getenv("AMAP_API_KEY", "").strip()
    if not key:
        raise RuntimeError("未读取到 AMAP_API_KEY，请检查项目根目录 .env")
    return key


def _request_json(url: str, params: Dict[str, Any], timeout: float = 2.0) -> Dict[str, Any]:
    # connect/read timeout 分离，避免卡住
    resp = requests.get(url, params=params, timeout=(0.8, timeout))
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"高德接口返回异常: {data}")
    return data


# =========================
# POI Search
# =========================
def _search_once(city: str, keywords: str, offset: int, type_codes: str = "", timeout_sec: float = 2.0) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "key": _get_key(),
        "keywords": keywords,
        "city": city,
        "citylimit": "true",
        "offset": max(1, min(int(offset), 25)),
        "page": 1,
        "extensions": "all",
    }
    if type_codes:
        params["types"] = type_codes

    data = _request_json(AMAP_POI_URL, params=params, timeout=timeout_sec)
    if str(data.get("status")) != "1":
        raise RuntimeError(f"高德POI接口失败: {_as_text(data.get('info')) or 'unknown error'}")
    pois = data.get("pois", [])
    return pois if isinstance(pois, list) else []


def _extract_sub_category(type_text: str) -> str:
    t = _as_text(type_text)
    if not t:
        return ""
    parts = [x.strip() for x in t.split(";") if x.strip()]
    return parts[-1] if parts else t


def _infer_category_from_type(type_text: str) -> str:
    t = _as_text(type_text)
    if not t:
        return ""
    for cat, kws in TYPE_INFER_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return cat
    return ""


def _normalize_poi(raw: Dict[str, Any], requested_category: str, fallback_city: str, fallback_area: str) -> Dict[str, Any]:
    biz_ext = raw.get("biz_ext", {})
    if not isinstance(biz_ext, dict):
        biz_ext = {}

    lat, lng = _parse_location(_as_text(raw.get("location")))
    rating = _to_float(biz_ext.get("rating", raw.get("rating")), 4.3)
    if rating <= 0:
        rating = 4.3

    avg_price = _to_int(biz_ext.get("cost"), 0)
    business_hours = (
        _as_text(raw.get("business_hours"))
        or _as_text(raw.get("open_time"))
        or _as_text(biz_ext.get("open_time"))
    )

    raw_id = _as_text(raw.get("id"))
    if not raw_id:
        raw_id = f"tmp_{abs(hash((_as_text(raw.get('name')), _as_text(raw.get('location'))))) % (10**10)}"

    poi = dict(SCHEMA_DEFAULTS)
    poi.update(
        {
            "id": f"amap_{raw_id}",
            "name": _as_text(raw.get("name")),
            "category": requested_category,
            "sub_category": _extract_sub_category(_as_text(raw.get("type"))),
            "address": _as_text(raw.get("address")),
            "city": _as_text(raw.get("cityname")) or fallback_city,
            "area": _as_text(raw.get("adname")) or fallback_area,
            "lat": lat,
            "lng": lng,
            "rating": rating,
            "avg_price_per_person": avg_price,
            "business_hours": business_hours,
        }
    )

    for f in SCHEMA_FIELDS:
        if f not in poi:
            poi[f] = SCHEMA_DEFAULTS[f]
    return {f: poi[f] for f in SCHEMA_FIELDS}


def search_poi(
    city: str,
    area: str,
    category: str,
    limit: int = 10,
    keyword_override: Optional[str] = None,
    timeout_sec: float = 2.0,
) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 20))
    city = _as_text(city) or "上海"
    area = _as_text(area)

    core_category = _map_to_core_category(category)
    keyword = _as_text(keyword_override) or CATEGORY_KEYWORDS.get(core_category, core_category)
    keywords = f"{area} {keyword}".strip() if area else keyword

    fetch_size = min(max(limit * 3, 12), 25)
    strict_codes = CATEGORY_TYPE_CODES.get(core_category, "")
    query_codes = [strict_codes, ""] if strict_codes else [""]

    enforce_filter = core_category in {"餐饮", "文化", "娱乐"}

    seen = set()
    ranked: List[Tuple[int, float, str, str, Dict[str, Any]]] = []
    last_error: Optional[Exception] = None

    for codes in query_codes:
        try:
            raws = _search_once(city=city, keywords=keywords, offset=fetch_size, type_codes=codes, timeout_sec=timeout_sec)
        except Exception as e:
            last_error = e
            continue

        for raw in raws:
            inferred = _infer_category_from_type(_as_text(raw.get("type")))
            if enforce_filter and inferred and inferred != core_category:
                continue

            poi = _normalize_poi(raw, requested_category=core_category, fallback_city=city, fallback_area=area)
            if not _as_text(poi.get("name")):
                continue

            dedupe_key = (
                _as_text(poi.get("name")),
                _as_text(poi.get("address")),
                round(_to_float(poi.get("lat"), 0.0), 6),
                round(_to_float(poi.get("lng"), 0.0), 6),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            poi_area = _as_text(poi.get("area"))
            poi_addr = _as_text(poi.get("address"))
            area_match = 1 if (area and (area in poi_area or area in poi_addr)) else 0
            rating = _to_float(poi.get("rating"), 0.0)

            ranked.append((area_match, rating, _as_text(poi.get("name")), _as_text(poi.get("address")), poi))
            if len(ranked) >= limit:
                break

        if len(ranked) >= limit:
            break

    if not ranked and last_error is not None:
        raise last_error

    ranked.sort(key=lambda x: (-x[0], -x[1], x[2], x[3]))
    return [x[4] for x in ranked[:limit]]


# =========================
# Constraints
# =========================
def filter_pois_by_constraints(pois: List[Dict[str, Any]], constraints: Dict[str, Any]) -> List[Dict[str, Any]]:
    min_rating = _to_float(constraints.get("min_rating"), 0.0)
    max_avg_price = _to_int(constraints.get("max_avg_price_per_person"), 10**9)
    min_sales = _to_int(constraints.get("min_half_year_sales"), 0)
    max_queue_risk = _as_text(constraints.get("max_queue_risk")) or "高"
    require_group_buy = bool(constraints.get("require_group_buy", False))

    if max_queue_risk not in RISK_RANK:
        max_queue_risk = "高"

    out: List[Dict[str, Any]] = []
    for p in pois:
        if _to_float(p.get("rating"), 0.0) < min_rating:
            continue
        if _to_int(p.get("avg_price_per_person"), 0) > max_avg_price:
            continue
        if _to_int(p.get("half_year_sales"), 0) < min_sales:
            continue

        qr = _as_text(p.get("queue_risk")) or "中"
        if RISK_RANK.get(qr, 2) > RISK_RANK[max_queue_risk]:
            continue

        if require_group_buy and not bool(p.get("has_group_buy", False)):
            continue

        out.append(p)

    out.sort(key=lambda x: (-_to_float(x.get("rating"), 0.0), -_to_int(x.get("half_year_sales"), 0), _as_text(x.get("name"))))
    return out


# =========================
# Routing helpers
# =========================
def _split_polyline(polyline: Any) -> List[str]:
    txt = _as_text(polyline)
    if not txt:
        return []
    return [p for p in txt.split(";") if "," in p]


def _append_points(target: List[str], extra: List[str]) -> None:
    for p in extra:
        if not target or target[-1] != p:
            target.append(p)


def _extract_v3_route_points(data: Dict[str, Any]) -> List[str]:
    points: List[str] = []
    paths = ((data.get("route") or {}).get("paths") or [])
    if not paths:
        return points
    first = paths[0] if isinstance(paths[0], dict) else {}
    for step in first.get("steps") or []:
        if isinstance(step, dict):
            _append_points(points, _split_polyline(step.get("polyline")))
    if not points:
        _append_points(points, _split_polyline(first.get("polyline")))
    return points


def _extract_transit_points(data: Dict[str, Any]) -> List[str]:
    points: List[str] = []
    transits = ((data.get("route") or {}).get("transits") or [])
    if not transits:
        return points
    first = transits[0] if isinstance(transits[0], dict) else {}
    segments = first.get("segments") or []
    for seg in segments:
        if not isinstance(seg, dict):
            continue

        walking = seg.get("walking") or {}
        for step in walking.get("steps") or []:
            if isinstance(step, dict):
                _append_points(points, _split_polyline(step.get("polyline")))

        bus = seg.get("bus") or {}
        for line in bus.get("buslines") or []:
            if isinstance(line, dict):
                _append_points(points, _split_polyline(line.get("polyline")))

        rail = seg.get("railway") or {}
        if isinstance(rail, dict):
            _append_points(points, _split_polyline(rail.get("polyline")))

        taxi = seg.get("taxi") or {}
        if isinstance(taxi, dict):
            _append_points(points, _split_polyline(taxi.get("polyline")))
    return points


def _extract_bicycling_points(data: Dict[str, Any]) -> List[str]:
    points: List[str] = []
    container = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(container, dict):
        return points
    paths = container.get("paths") or []
    if not paths:
        return points
    first = paths[0] if isinstance(paths[0], dict) else {}
    for step in first.get("steps") or []:
        if isinstance(step, dict):
            _append_points(points, _split_polyline(step.get("polyline")))
    if not points:
        _append_points(points, _split_polyline(first.get("polyline")))
    return points


def _fetch_route_polyline(origin: str, destination: str, mode: str = "walking", city: str = "", timeout_sec: float = 2.0) -> List[str]:
    mode = normalize_route_mode(mode)
    cache_key = (mode, origin, destination, city)
    if cache_key in _ROUTE_POLYLINE_CACHE:
        return _ROUTE_POLYLINE_CACHE[cache_key]

    fallback = [origin, destination]
    points: List[str] = []
    try:
        if mode == "walking":
            data = _request_json(AMAP_WALK_ROUTE_URL, {"key": _get_key(), "origin": origin, "destination": destination}, timeout=timeout_sec)
            if str(data.get("status")) == "1":
                points = _extract_v3_route_points(data)

        elif mode == "driving":
            data = _request_json(
                AMAP_DRIVE_ROUTE_URL,
                {"key": _get_key(), "origin": origin, "destination": destination, "strategy": "0", "extensions": "base"},
                timeout=timeout_sec,
            )
            if str(data.get("status")) == "1":
                points = _extract_v3_route_points(data)

        elif mode == "transit":
            params: Dict[str, Any] = {
                "key": _get_key(),
                "origin": origin,
                "destination": destination,
                "strategy": "0",
                "nightflag": "0",
                "extensions": "base",
            }
            if city:
                params["city"] = city
                params["cityd"] = city
            data = _request_json(AMAP_TRANSIT_ROUTE_URL, params, timeout=timeout_sec)
            if str(data.get("status")) == "1":
                points = _extract_transit_points(data)

        else:  # bicycling
            data = _request_json(AMAP_BIKE_ROUTE_URL, {"key": _get_key(), "origin": origin, "destination": destination}, timeout=timeout_sec)
            if "errcode" in data and str(data.get("errcode")) not in ("0", "10000"):
                points = []
            elif "status" in data and str(data.get("status")) != "1":
                points = []
            else:
                points = _extract_bicycling_points(data)
    except Exception:
        points = []

    if len(points) < 2:
        points = fallback

    _ROUTE_POLYLINE_CACHE[cache_key] = points
    return points


# =========================
# Time estimation (fast + fallback)
# =========================
def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def approx_segment_minutes(from_poi: Dict[str, Any], to_poi: Dict[str, Any], mode: str = "walking") -> int:
    lat1 = _to_float(from_poi.get("lat"), 0.0)
    lng1 = _to_float(from_poi.get("lng"), 0.0)
    lat2 = _to_float(to_poi.get("lat"), 0.0)
    lng2 = _to_float(to_poi.get("lng"), 0.0)
    if not all([lat1, lng1, lat2, lng2]):
        return 30

    km = _haversine_km(lat1, lng1, lat2, lng2)
    speed = {
        "walking": 4.5,
        "bicycling": 12.0,
        "driving": 28.0,
        "transit": 22.0,
    }.get(normalize_route_mode(mode), 20.0)
    # 1.25 绕路系数
    return max(1, int((km / speed) * 60 * 1.25))


def _extract_duration_seconds_from_v3_paths(data: Dict[str, Any]) -> int:
    paths = ((data.get("route") or {}).get("paths") or [])
    if not paths:
        return 0
    first = paths[0] if isinstance(paths[0], dict) else {}
    return _to_int(first.get("duration"), 0)


def _extract_duration_seconds_from_transit(data: Dict[str, Any]) -> int:
    transits = ((data.get("route") or {}).get("transits") or [])
    if not transits:
        return 0
    first = transits[0] if isinstance(transits[0], dict) else {}
    sec = _to_int(first.get("duration"), 0)
    if sec > 0:
        return sec

    total = 0
    for seg in first.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        total += _to_int((seg.get("walking") or {}).get("duration"), 0)
        total += _to_int((seg.get("bus") or {}).get("duration"), 0)
        total += _to_int((seg.get("railway") or {}).get("time"), 0)
        total += _to_int((seg.get("taxi") or {}).get("duration"), 0)
    return total


def _extract_duration_seconds_from_bicycling(data: Dict[str, Any]) -> int:
    container = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(container, dict):
        return 0
    paths = container.get("paths") or []
    if not paths:
        return 0
    first = paths[0] if isinstance(paths[0], dict) else {}
    return _to_int(first.get("duration"), 0)


def estimate_segment_minutes(
    from_poi: Dict[str, Any],
    to_poi: Dict[str, Any],
    mode: str = "walking",
    city: str = "",
    request_timeout_sec: float = 2.0,
    deadline_ts: Optional[float] = None,
) -> Optional[int]:
    mode = normalize_route_mode(mode)

    if deadline_ts is not None and time.time() >= deadline_ts:
        return approx_segment_minutes(from_poi, to_poi, mode)

    from_lng = _to_float(from_poi.get("lng"), 0.0)
    from_lat = _to_float(from_poi.get("lat"), 0.0)
    to_lng = _to_float(to_poi.get("lng"), 0.0)
    to_lat = _to_float(to_poi.get("lat"), 0.0)
    if not all([from_lng, from_lat, to_lng, to_lat]):
        return None

    origin = f"{from_lng:.6f},{from_lat:.6f}"
    destination = f"{to_lng:.6f},{to_lat:.6f}"
    city_hint = _as_text(city) or _as_text(from_poi.get("city")) or _as_text(to_poi.get("city"))

    cache_key = (mode, origin, destination, city_hint)
    if cache_key in _SEGMENT_DURATION_CACHE:
        return _SEGMENT_DURATION_CACHE[cache_key]

    sec = 0
    try:
        if mode == "walking":
            data = _request_json(AMAP_WALK_ROUTE_URL, {"key": _get_key(), "origin": origin, "destination": destination}, timeout=request_timeout_sec)
            if str(data.get("status")) == "1":
                sec = _extract_duration_seconds_from_v3_paths(data)

        elif mode == "driving":
            data = _request_json(
                AMAP_DRIVE_ROUTE_URL,
                {"key": _get_key(), "origin": origin, "destination": destination, "strategy": "0", "extensions": "base"},
                timeout=request_timeout_sec,
            )
            if str(data.get("status")) == "1":
                sec = _extract_duration_seconds_from_v3_paths(data)

        elif mode == "transit":
            params: Dict[str, Any] = {
                "key": _get_key(),
                "origin": origin,
                "destination": destination,
                "strategy": "0",
                "nightflag": "0",
                "extensions": "base",
            }
            if city_hint:
                params["city"] = city_hint
                params["cityd"] = city_hint
            data = _request_json(AMAP_TRANSIT_ROUTE_URL, params, timeout=request_timeout_sec)
            if str(data.get("status")) == "1":
                sec = _extract_duration_seconds_from_transit(data)

        else:  # bicycling
            data = _request_json(AMAP_BIKE_ROUTE_URL, {"key": _get_key(), "origin": origin, "destination": destination}, timeout=request_timeout_sec)
            if "errcode" in data and str(data.get("errcode")) not in ("0", "10000"):
                sec = 0
            else:
                sec = _extract_duration_seconds_from_bicycling(data)

    except Exception:
        return approx_segment_minutes(from_poi, to_poi, mode)

    if sec <= 0:
        return approx_segment_minutes(from_poi, to_poi, mode)

    minutes = max(1, (sec + 59) // 60)
    _SEGMENT_DURATION_CACHE[cache_key] = minutes
    return minutes


def estimate_route_travel_minutes(
    route: List[Dict[str, Any]],
    mode: str = "walking",
    request_timeout_sec: float = 2.0,
    deadline_ts: Optional[float] = None,
) -> Optional[int]:
    if len(route) < 2:
        return 0
    total = 0
    for a, b in zip(route[:-1], route[1:]):
        seg = estimate_segment_minutes(
            a,
            b,
            mode=mode,
            city=_as_text(a.get("city")) or _as_text(b.get("city")),
            request_timeout_sec=request_timeout_sec,
            deadline_ts=deadline_ts,
        )
        if seg is None:
            return None
        total += seg
    return total


# =========================
# Static map
# =========================
def _downsample_points(points: List[str], max_points: int = 220) -> List[str]:
    if len(points) <= max_points:
        return points
    step = (len(points) // max_points) + 1
    sampled = points[::step]
    if sampled and points and sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def build_static_map_url(
    route: List[Dict[str, Any]],
    size: str = "900*500",
    use_roadnet: bool = True,
    route_mode: str = "walking",
    segment_modes: Optional[List[str]] = None,
    api_timeout_sec: float = 2.0,
) -> str:
    if not route:
        return ""

    default_mode = normalize_route_mode(route_mode)
    valid_points: List[Dict[str, Any]] = []
    markers: List[str] = []

    for i, poi in enumerate(route[:8], start=1):
        lat = _to_float(poi.get("lat"), 0.0)
        lng = _to_float(poi.get("lng"), 0.0)
        if lat == 0.0 or lng == 0.0:
            continue
        valid_points.append({"lat": lat, "lng": lng, "city": _as_text(poi.get("city"))})
        markers.append(f"mid,0xFF6A00,{i}:{lng},{lat}")

    if not valid_points:
        return ""

    params = [f"key={_get_key()}", f"size={size}", "scale=2"]
    if markers:
        params.append("markers=" + quote("|".join(markers), safe=",:|"))

    if len(valid_points) >= 2:
        if use_roadnet:
            merged_points: List[str] = []
            for idx, (a, b) in enumerate(zip(valid_points[:-1], valid_points[1:])):
                seg_mode = default_mode
                if segment_modes and idx < len(segment_modes):
                    seg_mode = normalize_route_mode(segment_modes[idx])

                origin = f"{a['lng']},{a['lat']}"
                destination = f"{b['lng']},{b['lat']}"
                city_hint = a.get("city") or b.get("city") or ""

                seg = _fetch_route_polyline(
                    origin=origin,
                    destination=destination,
                    mode=seg_mode,
                    city=city_hint,
                    timeout_sec=api_timeout_sec,
                )
                _append_points(merged_points, seg)

            poly_points = _downsample_points(merged_points, max_points=220)
        else:
            poly_points = [f"{p['lng']},{p['lat']}" for p in valid_points]

        if len(poly_points) >= 2:
            style = ROUTE_MODE_STYLE.get(default_mode, ROUTE_MODE_STYLE["walking"])
            path = f"{style['weight']},{style['color']},1,,:{';'.join(poly_points)}"
            params.append("paths=" + quote(path, safe=",:;|"))

    return AMAP_STATIC_MAP_URL + "?" + "&".join(params)


def build_constraints_from_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "transport_mode": _as_text(intent.get("transport_mode")) or "walking",
        "max_total_minutes": _to_int(intent.get("max_total_minutes"), 10**9),
        "min_rating": _to_float(intent.get("min_rating"), 0.0),
        "budget": _to_int(intent.get("budget"), 10**9),
        "party_size": max(1, _to_int(intent.get("party_size"), 1)),
        "max_queue_risk": _as_text(intent.get("max_queue_risk")) or "高",
        "require_group_buy": bool(intent.get("require_group_buy", False)),
        "min_half_year_sales": _to_int(intent.get("min_half_year_sales"), 0),
        "max_avg_price_per_person": _to_int(intent.get("max_avg_price_per_person"), 10**9),
        "default_stay_minutes": _to_int(intent.get("default_stay_minutes"), 60),
    }


__all__ = [
    "SCHEMA_FIELDS",
    "CATEGORY_KEYWORDS",
    "normalize_route_mode",
    "search_poi",
    "filter_pois_by_constraints",
    "approx_segment_minutes",
    "estimate_segment_minutes",
    "estimate_route_travel_minutes",
    "build_static_map_url",
    "build_constraints_from_intent",
]
