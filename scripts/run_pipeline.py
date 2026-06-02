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

DEFAULT_INPUT = "帮我规划上海外滩附近的周末下午，预算300元，想吃本帮菜，顺便逛文化景点"

user_input = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
print(f"\n用户输入: {user_input}\n")

initial_state: RouteState = {
    "user_input": user_input,
    "intent": {},
    "candidates": {},
    "route": [],
    "locked_nodes": [],
    "map_url": "",
    "summary": "",
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
