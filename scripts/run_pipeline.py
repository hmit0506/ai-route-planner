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

# Common Simplified-only characters (Traditional equivalents differ)
_SIMP_ONLY = frozenset(
    "来为与时间这对应现经联复观标处专务说问动传统约点东爱试场临规则设计划备将过质实际"
    "头发样没关转历义务应该简单问题结报告网络软件系统环境开发测试运行管理"
    "买卖进出发展变化认识态情况条标进行设备装置产品服务会国说话边"
)

def _detect_lang(text: str) -> str:
    ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
    if ascii_ratio > 0.8:
        return "en"
    if any(c in _SIMP_ONLY for c in text):
        return "zh-CN"
    return "zh-TW"

language = sys.argv[2] if len(sys.argv) > 2 else _detect_lang(user_input)
_INPUT_LABEL = {"zh-CN": "用户输入", "zh-TW": "用戶輸入", "en": "User input"}
from route_planner.i18n import normalize as _norm
print(f"\n{_INPUT_LABEL.get(_norm(language), '用戶輸入')}: {user_input}\n")

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

_HEADERS = {
    "zh-CN": ("=== Agent 日志 ===", "=== 路线结果 ===", "=== 总结 ===", "=== 地图URL ==="),
    "zh-TW": ("=== Agent 日誌 ===", "=== 路線結果 ===", "=== 總結 ===", "=== 地圖URL ==="),
    "en":    ("=== Agent Log ===",  "=== Route Result ===", "=== Summary ===", "=== Map URL ==="),
}
from route_planner.i18n import normalize as _norm
h_log, h_route, h_summary, h_map = _HEADERS[_norm(language)]

print(h_log)
for step in result["stream_updates"]:
    print(f"  • {step}")

print(f"\n{h_route}")
for poi in result["route"]:
    gb = poi.get("group_buy")
    gb_str = f" | {gb['current_price']} HKD" if gb else ""
    print(f"  {poi['order']}. {poi['name']} ({poi['category']}) "
          f"| {poi['rating']} | {poi['queue_risk']}{gb_str}")
    print(f"     {poi.get('stay_minutes',60)}min → {poi.get('transport_to_next','')}")

print(f"\n{h_summary}\n{result['summary']}")
print(f"\n{h_map}\n{result['map_url']}")
