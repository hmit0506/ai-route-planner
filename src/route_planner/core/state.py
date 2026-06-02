from typing import TypedDict, List, Dict, Any


class RouteState(TypedDict):
    user_input: str
    intent: Dict[str, Any]
    candidates: Dict[str, Any]
    route: List[Dict[str, Any]]
    locked_nodes: List[int]
    map_url: str
    summary: str
    conversation_history: List[Dict[str, str]]
    stream_updates: List[str]
