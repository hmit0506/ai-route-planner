from langgraph.graph import StateGraph, END

from route_planner.core.state import RouteState
from route_planner.nodes.intent import IntentNode
from route_planner.nodes.poi_search import POISearchNode
from route_planner.nodes.route import RouteNode
from route_planner.nodes.enrich import EnrichNode
from route_planner.nodes.output import OutputNode


def build_graph() -> StateGraph:
    graph = StateGraph(RouteState)

    graph.add_node("intent", IntentNode())
    graph.add_node("poi_search", POISearchNode())
    graph.add_node("route", RouteNode())
    graph.add_node("enrich", EnrichNode())
    graph.add_node("output", OutputNode())

    graph.set_entry_point("intent")
    graph.add_edge("intent", "poi_search")
    graph.add_edge("poi_search", "route")
    graph.add_edge("route", "enrich")
    graph.add_edge("enrich", "output")
    graph.add_edge("output", END)

    return graph.compile()
