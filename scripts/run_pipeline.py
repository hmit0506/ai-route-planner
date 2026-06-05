"""
Smoke test: run full pipeline and print the final route.

Usage:
    python scripts/run_pipeline.py
    python scripts/run_pipeline.py "帮我找北京三里屯周六晚上，预算500元，想吃火锅"
"""
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from route_planner.graph import build_graph
from route_planner.state import RouteState

DEFAULT_INPUT = "旺角附近下午，想吃日本料理，預算400港幣"

user_input = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT

# Auto-detect language from input: if mostly ASCII → English, else Traditional Chinese
def _detect_lang(text: str) -> str:
    ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
    if ascii_ratio > 0.8:
        return "en"
    # Simplified Chinese characters are in range 0x4E00-0x9FFF but so are Traditional;
    # check for simplified-only characters as a heuristic
    simplified_chars = set("简体繁体规划预算吃饭餐饮")
    if any(c in simplified_chars for c in text):
        return "zh-CN"
    return "zh-TW"

language = sys.argv[2] if len(sys.argv) > 2 else _detect_lang(user_input)
print(f"\n用户输入: {user_input}  [language={language}]\n")

initial_state: RouteState = {
    "user_input": user_input,
    "language": language,
    "intent": {},
    "candidates": {},
    "route": [],
    "locked_nodes": [],
    "map_url": "",
    "summary": "",
    "fulfillment_notes": {},
    "conversation_history": [],
    "stream_updates": [],
}

graph = build_graph()
result = graph.invoke(initial_state)

print("=== Agent 日志 ===")
for step in result["stream_updates"]:
    print(f"  • {step}")

print("\n=== 路线结果 ===")
for poi in result["route"]:
    gb = poi.get("group_buy")
    gb_str = f" | 团购:{gb['current_price']}元" if gb else ""
    print(f"  {poi['order']}. {poi['name']} ({poi['category']}) "
          f"| 评分:{poi['rating']} | 等位:{poi['queue_risk']}{gb_str}")
    print(f"     停留:{poi['stay_minutes']}分钟 → {poi['transport_to_next']}")

print(f"\n=== 总结 ===\n{result['summary']}")
print(f"\n=== 地图URL ===\n{result['map_url']}")
