import math
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState

# Average minutes per stop (stay + transit to next)
_AVG_MINUTES_PER_STOP = 75
# Max radius (km) to keep POIs around centroid
_MAX_RADIUS_KM = 3.0


def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _centroid(pois):
    lats = [p["lat"] for p in pois if p.get("lat")]
    lngs = [p["lng"] for p in pois if p.get("lng")]
    if not lats:
        return None, None
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


class GeoClusterNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        candidates = state["candidates"]
        intent = state["intent"]
        duration_hours = intent.get("duration_hours", 4)

        # Compute max POIs based on time budget
        max_pois = max(3, min(5, int(duration_hours * 60 / _AVG_MINUTES_PER_STOP)))

        # Pool all candidates to find geographic center
        all_pois = [p for pois in candidates.values() for p in pois]
        center_lat, center_lng = _centroid(all_pois)

        # Filter each category to POIs within radius of centroid
        filtered: dict[str, list] = {}
        if center_lat is not None:
            for cat, pois in candidates.items():
                nearby = [
                    p for p in pois
                    if _haversine_km(center_lat, center_lng, p["lat"], p["lng"]) <= _MAX_RADIUS_KM
                ]
                # Keep at least 3 per category even if radius filters too much
                filtered[cat] = nearby if len(nearby) >= 3 else pois
        else:
            filtered = candidates

        # Write max_pois back to intent for RouteAgent to use
        updated_intent = {**intent, "max_pois": max_pois}

        updates = list(state.get("stream_updates", []))
        updates.append(f"地理聚合完成：中心半径{_MAX_RADIUS_KM}km，时间预算{duration_hours}小时→最多{max_pois}站")

        return {**state, "candidates": filtered, "intent": updated_intent, "stream_updates": updates}
