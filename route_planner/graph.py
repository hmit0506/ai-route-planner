from langgraph.graph import StateGraph, END

from route_planner.state import RouteState
from route_planner.nodes.intent import IntentNode
from route_planner.nodes.weather import WeatherNode
from route_planner.nodes.poi_search import POISearchNode
from route_planner.nodes.geo_cluster import GeoClusterNode
from route_planner.nodes.route import RouteNode
from route_planner.nodes.enrich import EnrichNode
from route_planner.nodes.output import OutputNode
from route_planner.nodes.refine import RefineNode
from route_planner.nodes.refine_select import RefineSelectNode


def build_graph() -> StateGraph:
    graph = StateGraph(RouteState)

    graph.add_node("intent", IntentNode())
    graph.add_node("weather", WeatherNode())
    graph.add_node("poi_search", POISearchNode())
    graph.add_node("geo_cluster", GeoClusterNode())
    graph.add_node("route", RouteNode())
    graph.add_node("enrich", EnrichNode())
    graph.add_node("output", OutputNode())

    graph.set_entry_point("intent")
    graph.add_edge("intent", "weather")
    graph.add_edge("weather", "poi_search")
    graph.add_edge("poi_search", "geo_cluster")
    graph.add_edge("geo_cluster", "route")
    graph.add_edge("route", "enrich")
    graph.add_edge("enrich", "output")
    graph.add_edge("output", END)

    return graph.compile()


def build_refine_graph() -> StateGraph:
    """
    Refine graph for partial POI replacement ("换一家").
    Flow: refine (LLM) → poi_search → refine_select (code) → enrich → output
    Only 1 LLM call total (RefineNode).
    """
    graph = StateGraph(RouteState)

    graph.add_node("refine", RefineNode())
    graph.add_node("poi_search", POISearchNode())
    graph.add_node("refine_select", RefineSelectNode())
    graph.add_node("enrich", EnrichNode())
    graph.add_node("output", OutputNode())

    graph.set_entry_point("refine")
    graph.add_edge("refine", "poi_search")
    graph.add_edge("poi_search", "refine_select")
    graph.add_edge("refine_select", "enrich")
    graph.add_edge("enrich", "output")
    graph.add_edge("output", END)

    return graph.compile()
