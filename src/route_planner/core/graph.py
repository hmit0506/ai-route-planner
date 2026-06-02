from langgraph.graph import StateGraph, END

from route_planner.core.state import RouteState
from route_planner.nodes.intent import IntentNode


def build_graph() -> StateGraph:
    graph = StateGraph(RouteState)

    graph.add_node("intent", IntentNode())

    graph.set_entry_point("intent")
    graph.add_edge("intent", END)

    return graph.compile()
