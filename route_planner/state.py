from typing import TypedDict, List, Dict, Any


class RouteState(TypedDict):
    user_input: str
    language: str                        # "zh-TW" | "zh-CN" | "en"
    intent: Dict[str, Any]
    candidates: Dict[str, Any]
    route: List[Dict[str, Any]]
    locked_nodes: List[int]
    map_url: str
    summary: str
    fulfillment_notes: Dict[str, Any]   # satisfied / unmatched / tips
    conversation_history: List[Dict[str, str]]
    stream_updates: List[str]
    user_memory: Dict[str, Any]          # loaded from user_id; empty dict if anonymous
    weather: Dict[str, Any]             # WeatherNode output (condition/temp/prefer_indoor…)
    xiaohongshu_post: str               # OutputNode: social-media style export text
