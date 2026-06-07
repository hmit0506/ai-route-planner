import math
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
from route_planner.area_coords import get_area_center
import route_planner.i18n as i18n

_AVG_MINUTES_PER_STOP = 65
_MAX_RADIUS_KM = 2.0
_FALLBACK_RADIUS_KM = 8.0  # secondary radius before giving up geo-filtering entirely


def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
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

        max_pois = max(3, min(8, int(duration_hours * 60 / _AVG_MINUTES_PER_STOP)))

        # Prefer known area center as anchor; fall back to centroid of all candidates
        area = intent.get("area", "")
        anchor = get_area_center(area)
        if anchor:
            center_lat, center_lng = anchor
        else:
            all_pois = [p for pois in candidates.values() for p in pois]
            center_lat, center_lng = _centroid(all_pois)

        filtered: dict[str, list] = {}
        if center_lat is not None:
            for cat, pois in candidates.items():
                nearby = [
                    p for p in pois
                    if _haversine_km(center_lat, center_lng, p["lat"], p["lng"]) <= _MAX_RADIUS_KM
                ]
                if len(nearby) >= 3:
                    filtered[cat] = nearby
                else:
                    # Try a wider radius before falling back to unfiltered candidates
                    wider = [
                        p for p in pois
                        if _haversine_km(center_lat, center_lng, p["lat"], p["lng"]) <= _FALLBACK_RADIUS_KM
                    ]
                    filtered[cat] = wider if len(wider) >= 2 else pois
        else:
            filtered = candidates

        updated_intent = {**intent, "max_pois": max_pois}

        lang = state.get("language", "zh-TW")
        updates = list(state.get("stream_updates", []))
        updates.append(i18n.step("geo_done", lang,
            r=_MAX_RADIUS_KM, dur=duration_hours, n=max_pois))

        return {**state, "candidates": filtered, "intent": updated_intent, "stream_updates": updates}
