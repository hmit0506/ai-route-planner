"""
RefineSelectNode: pure-code node that slots one replacement POI into the locked route.

Reads:
  state["route"]        — current route (locked POIs already in correct positions)
  state["candidates"]   — search results for the category being replaced
  state["intent"]["_refine"]["replace_order"]  — 1-indexed slot to fill
  state["intent"]["_refine"]["new_constraints"] — optional filters

Writes:
  state["route"]  — merged route: locked POIs + the single replacement
"""
from typing import Dict, Any

from route_planner.node import BaseNode
from route_planner.state import RouteState
import route_planner.i18n as i18n
from route_planner.nodes.poi_search import _normalize_cat


def _passes_constraints(poi: dict, constraints: dict) -> bool:
    if not constraints:
        return True
    if "queue_risk" in constraints:
        allowed = {"低": {"低"}, "中": {"低", "中"}}.get(constraints["queue_risk"], {"低", "中", "高"})
        if poi.get("queue_risk", "低") not in allowed:
            return False
    if "max_price" in constraints:
        if poi.get("avg_price_per_person", 0) > constraints["max_price"]:
            return False
    if "avoid_sub_category" in constraints:
        if poi.get("sub_category", "") in constraints["avoid_sub_category"]:
            return False
    return True


class RefineSelectNode(BaseNode):
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        route = list(state.get("route", []))
        candidates = state.get("candidates", {})
        refine_meta = state.get("intent", {}).get("_refine", {})

        replace_order = int(refine_meta.get("replace_order", 1))
        new_constraints = refine_meta.get("new_constraints", {})

        # Exclude ALL current route POIs (both locked and the one being replaced)
        # to ensure we always pick a genuinely new POI
        locked_ids = {
            p.get("poi_id") or p.get("id", "")
            for p in route
        }

        # Find replacement category's candidates (normalize translated names to internal Chinese)
        replace_category = _normalize_cat(refine_meta.get("category", ""))
        pool = candidates.get(replace_category, [])
        if not pool:
            # Fallback: search across all candidate categories
            for pois in candidates.values():
                pool.extend(pois)

        # Filter: not already in route, passes constraints, sorted by rating
        pool = [
            p for p in pool
            if (p.get("id") or p.get("poi_id", "")) not in locked_ids
            and _passes_constraints(p, new_constraints)
        ]
        prefer_subs = new_constraints.get("prefer_sub_category", [])
        pool.sort(
            key=lambda x: (
                0 if any(p in x.get("sub_category", "") for p in prefer_subs) else 1,
                -x.get("rating", 0),
            )
        )

        if not pool:
            lang = state.get("language", "zh-TW")
            updates = list(state.get("stream_updates", []))
            updates.append(i18n.step("refine_no_result", lang))
            return {**state, "stream_updates": updates}

        best = pool[0]
        replacement = {
            "poi_id": best["id"],
            "order": replace_order,
            "stay_minutes": 60 if best.get("category") != "餐饮" else 90,
        }

        # Rebuild route: keep locked POIs, insert replacement at correct order
        new_route = [
            p if p.get("order", 0) != replace_order else replacement
            for p in route
        ]
        # If replace_order wasn't in route (shouldn't happen), append
        orders_present = {p.get("order", 0) for p in new_route}
        if replace_order not in orders_present:
            new_route.append(replacement)
        new_route.sort(key=lambda x: x.get("order", 0))

        lang = state.get("language", "zh-TW")
        updates = list(state.get("stream_updates", []))
        updates.append(i18n.step("refine_replaced", lang, name=best["name"]))

        return {**state, "route": new_route, "stream_updates": updates}
