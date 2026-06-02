import math
import os
from typing import Dict, Any

from route_planner.core.node import BaseNode
from route_planner.core.state import RouteState


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


def _build_map_url(route: list) -> str:
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
    return (
        f"https://restapi.amap.com/v3/staticmap"
        f"?location={avg_lng:.4f},{avg_lat:.4f}"
        f"&zoom=14&size=750*400"
        f"&markers={markers}"
        f"&key={api_key}"
    )


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
        route = [dict(r) for r in state["route"]]  # shallow copy

        for i, poi in enumerate(route):
            poi["order"] = i + 1
            if i < len(route) - 1:
                nxt = route[i + 1]
                km = _haversine_km(poi["lat"], poi["lng"], nxt["lat"], nxt["lng"])
                poi["transport_to_next"] = _transport_text(km)
            else:
                poi["transport_to_next"] = ""

        map_url = _build_map_url(route)
        summary = _build_summary(route)

        updates = list(state.get("stream_updates", []))
        updates.append("路线规划完成，已生成地图链接")

        return {
            **state,
            "route": route,
            "map_url": map_url,
            "summary": summary,
            "stream_updates": updates,
        }
