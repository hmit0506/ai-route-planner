import math
import os
import urllib.request
import urllib.parse
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _transport_text(km: float) -> str:
    if km <= 1.5:
        return f"步行约{max(5, round(km * 15))}分钟"
    if km <= 5.0:
        return f"骑行/打车约{max(8, round(km * 4))}分钟"
    return f"打车约{max(15, round(km * 3))}分钟"


def _fetch_walking_polyline(
    origin_lng: float, origin_lat: float,
    dest_lng: float, dest_lat: float,
    api_key: str,
) -> str | None:
    """Call Amap walking directions API; return semicolon-separated 'lng,lat' polyline or None."""
    params = urllib.parse.urlencode({
        "origin": f"{origin_lng},{origin_lat}",
        "destination": f"{dest_lng},{dest_lat}",
        "key": api_key,
    })
    url = f"https://restapi.amap.com/v3/direction/walking?{params}"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
        if data.get("status") != "1":
            return None
        steps = data["route"]["paths"][0]["steps"]
        # Each step has a polyline string "lng,lat;lng,lat;..."
        full = ";".join(s["polyline"] for s in steps)
        return _downsample(full, max_points=40)
    except Exception:
        return None


def _downsample(polyline: str, max_points: int) -> str:
    """Reduce number of points to keep static map URL under length limit."""
    pts = polyline.split(";")
    if len(pts) <= max_points:
        return polyline
    step = len(pts) / max_points
    kept = [pts[round(i * step)] for i in range(max_points)]
    # Always include last point
    if kept[-1] != pts[-1]:
        kept[-1] = pts[-1]
    return ";".join(kept)


def _build_map_url(route: list, polylines: list[str | None]) -> str:
    if not route:
        return ""
    api_key = os.getenv("AMAP_API_KEY", "YOUR_AMAP_KEY")
    labels = "ABCDEFGHIJ"
    markers = "|".join(
        f"mid,,{labels[i] if i < len(labels) else i+1}:{p['lng']},{p['lat']}"
        for i, p in enumerate(route)
    )
    avg_lat = sum(p["lat"] for p in route) / len(route)
    avg_lng = sum(p["lng"] for p in route) / len(route)

    base = (
        f"https://restapi.amap.com/v3/staticmap"
        f"?location={avg_lng:.4f},{avg_lat:.4f}"
        f"&zoom=14&size=750*400"
        f"&markers={markers}"
        f"&key={api_key}"
    )

    # Append each walking path segment
    path_parts = []
    for polyline in polylines:
        if polyline:
            path_parts.append(f"4,0x0065FF,0.7,,:{polyline}")
    if path_parts:
        base += "&paths=" + "|".join(path_parts)

    return base


def _nav_url(poi: dict) -> str:
    name = urllib.parse.quote(poi.get("name", ""))
    return (
        f"https://uri.amap.com/navigation"
        f"?to={poi['lng']},{poi['lat']},{name}"
        f"&mode=walk&coordinate=gaode&callnative=1"
    )


def _build_fulfillment(route: list, intent: dict) -> dict:
    """Compare actual route against user intent, report satisfied/unmatched/tips."""
    food_pref      = intent.get("food_pref", [])
    culture_pref   = intent.get("culture_pref", [])
    dining_count_r = intent.get("dining_count", 0)
    avoid          = intent.get("avoid", [])

    satisfied, unmatched, tips = [], [], []

    # Check dining_count
    dining_pois = [p for p in route if p.get("category") == "餐饮"]
    if dining_count_r > 0:
        if len(dining_pois) == dining_count_r:
            satisfied.append(f"餐饮安排 ✓ （{dining_count_r}个）")
        else:
            unmatched.append(
                f"餐饮不足：要求{dining_count_r}个，实际安排{len(dining_pois)}个餐饮站点"
            )
            tips.append("可说「再加一家餐厅」进行多轮调整")

    # Check food_pref matching
    if food_pref:
        matched_dining = [p for p in dining_pois if p.get("pref_matched")]
        unmatched_dining = [p for p in dining_pois if not p.get("pref_matched")]
        if matched_dining:
            names = "、".join(p["name"] for p in matched_dining)
            satisfied.append(f"餐饮偏好 {food_pref} ✓ （{names}）")
        if unmatched_dining:
            names = "、".join(p["name"] for p in unmatched_dining)
            subs  = "、".join(p.get("sub_category","") for p in unmatched_dining)
            unmatched.append(
                f"未找到 {food_pref} 餐厅，以 {subs}（{names}）替代"
            )
            tips.append(f"该商圈暂无 {'、'.join(food_pref)} 餐厅；可在多轮对话中说「换一家 {'或'.join(food_pref)} 餐厅」，系统会在全市范围内搜索")

    # Check culture_pref matching
    if culture_pref:
        cultural_pois = [p for p in route if p.get("category") in ("文化","娱乐","自然")]
        matched_cultural = [p for p in cultural_pois if p.get("pref_matched")]
        unmatched_cultural = [p for p in cultural_pois if not p.get("pref_matched")]
        if matched_cultural:
            names = "、".join(p["name"] for p in matched_cultural)
            satisfied.append(f"文化偏好 {culture_pref} ✓ （{names}）")
        if unmatched_cultural:
            names = "、".join(p["name"] for p in unmatched_cultural)
            subs  = "、".join(p.get("sub_category","") for p in unmatched_cultural)
            unmatched.append(f"未找到 {culture_pref} 类地点，以 {subs}（{names}）替代")
            tips.append(f"可在多轮对话中说「换一个 {'或'.join(culture_pref)}」")

    # Check avoid violations
    if avoid:
        violated = [p for p in route if any(a in p.get("sub_category","") for a in avoid)]
        if violated:
            names = "、".join(p["name"] for p in violated)
            unmatched.append(f"包含了你想避开的类型（{avoid}）：{names}")
            tips.append(f"可说「去掉 {names}」进行替换")

    return {
        "satisfied": satisfied,
        "unmatched": unmatched,
        "tips": tips,
    }


def _build_summary(route: list) -> str:
    n = len(route)
    total_mins = sum(r.get("stay_minutes", 60) for r in route)
    h, m = divmod(total_mins, 60)
    time_str = f"{h}小时{m}分钟" if m else f"{h}小时"
    gb_count = sum(1 for r in route if r.get("has_group_buy"))
    budget_used = sum(
        (r.get("group_buy") or {}).get("current_price", 0) or r.get("avg_price_per_person", 0)
        for r in route
        if r.get("category") == "餐饮"
    )
    gb_str = f"，{gb_count}处有团购优惠" if gb_count else ""
    return f"为你安排了{n}站行程，预计游玩{time_str}{gb_str}，餐饮消费约{budget_used}元。"


class OutputNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        route = [dict(r) for r in state["route"]]
        api_key = os.getenv("AMAP_API_KEY", "")

        # Fetch all walking polylines in parallel
        n_segments = len(route) - 1
        def _fetch_segment(i):
            poi, nxt = route[i], route[i + 1]
            return _fetch_walking_polyline(
                poi["lng"], poi["lat"], nxt["lng"], nxt["lat"], api_key
            ) if api_key else None

        if n_segments > 0:
            with ThreadPoolExecutor(max_workers=min(n_segments, 5)) as ex:
                polylines = list(ex.map(_fetch_segment, range(n_segments)))
        else:
            polylines = []

        for i, poi in enumerate(route):
            poi["order"] = i + 1
            poi["navigation_url"] = _nav_url(poi)
            if i < len(route) - 1:
                nxt = route[i + 1]
                km = _haversine_km(poi["lat"], poi["lng"], nxt["lat"], nxt["lng"])
                poi["transport_to_next"] = _transport_text(km)
                poi["transport_polyline"] = polylines[i]
            else:
                poi["transport_to_next"] = ""
                poi["transport_polyline"] = None

        map_url = _build_map_url(route, polylines)
        fulfillment = _build_fulfillment(route, state.get("intent", {}))
        summary = _build_summary(route)
        if fulfillment.get("unmatched"):
            summary += "（" + "；".join(fulfillment["unmatched"]) + "）"

        updates = list(state.get("stream_updates", []))
        updates.append("路线规划完成，已生成地图链接")

        # Surface fulfillment issues as visible step events
        for msg in fulfillment.get("unmatched", []):
            updates.append(f"⚠️ {msg}")
        for tip in fulfillment.get("tips", []):
            updates.append(f"💡 {tip}")

        return {
            **state,
            "route": route,
            "map_url": map_url,
            "summary": summary,
            "fulfillment_notes": fulfillment,
            "stream_updates": updates,
        }
