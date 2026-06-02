"""
Smoke test: run IntentAgent only and print parsed intent.

Usage:
    python scripts/run_intent.py
    python scripts/run_intent.py "帮我找北京三里屯附近的周六晚上，预算500元，想吃火锅"
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

print("=== IntentAgent 输出 ===")
print(json.dumps(result["intent"], ensure_ascii=False, indent=2))
print("\n=== Agent 日志 ===")
for step in result["stream_updates"]:
    print(f"  • {step}")
