import asyncio
import csv
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.schemas import RouteRequest
from route_planner.graph import build_graph, build_refine_graph
from route_planner.state import RouteState
import route_planner.user_memory as user_memory
import route_planner.i18n as i18n

_DATA_DIR = Path(__file__).parent.parent / "route_planner" / "data"
_CSV_PATH = _DATA_DIR / "poi.csv"
_DB_PATH  = _DATA_DIR / "poi.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS pois (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, name_en TEXT,
    category TEXT NOT NULL, sub_category TEXT, address TEXT, address_en TEXT,
    city TEXT, area TEXT, lat REAL, lng REAL,
    rating REAL, taste_rating REAL, decor_rating REAL, service_rating REAL,
    hygiene_rating REAL, value_rating REAL, review_count INTEGER,
    half_year_sales INTEGER, avg_price_per_person REAL,
    queue_risk TEXT, queue_minutes_peak INTEGER, queue_minutes_offpeak INTEGER,
    has_group_buy INTEGER, group_buy_title TEXT,
    group_buy_original_price REAL, group_buy_current_price REAL,
    business_hours TEXT, trend_tag TEXT, recommend_count INTEGER,
    queue_signal_level TEXT, risk_signal_level TEXT, photo_hotness_level TEXT,
    local_authenticity_level TEXT, scenario_tags TEXT,
    risk_mention_rate REAL, queue_mention_rate REAL,
    year_max INTEGER, photo_mention_rate REAL,
    local_mention_rate REAL, accessibility_mention_rate REAL
)
"""
_FIELDS = [
    "id","name","name_en","category","sub_category","address","address_en","city","area",
    "lat","lng","rating","taste_rating","decor_rating","service_rating","hygiene_rating",
    "value_rating","review_count","half_year_sales","avg_price_per_person","queue_risk",
    "queue_minutes_peak","queue_minutes_offpeak","has_group_buy","group_buy_title",
    "group_buy_original_price","group_buy_current_price","business_hours","trend_tag",
    "recommend_count",
    "queue_signal_level","risk_signal_level","photo_hotness_level",
    "local_authenticity_level","scenario_tags",
    "risk_mention_rate","queue_mention_rate",
    "year_max","photo_mention_rate","local_mention_rate","accessibility_mention_rate",
]
_REAL = {"lat","lng","rating","taste_rating","decor_rating","service_rating",
         "hygiene_rating","value_rating","avg_price_per_person",
         "group_buy_original_price","group_buy_current_price",
         "risk_mention_rate","queue_mention_rate",
         "photo_mention_rate","local_mention_rate","accessibility_mention_rate"}
_INT  = {"review_count","half_year_sales","queue_minutes_peak",
         "queue_minutes_offpeak","has_group_buy","recommend_count","year_max"}


def _ensure_db() -> None:
    if _DB_PATH.exists() and _DB_PATH.stat().st_mtime >= _CSV_PATH.stat().st_mtime:
        return
    with open(_CSV_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(_CREATE_SQL)
    conn.execute("DELETE FROM pois")
    ph = ", ".join("?" * len(_FIELDS))
    sql = f"INSERT OR REPLACE INTO pois ({', '.join(_FIELDS)}) VALUES ({ph})"
    for row in rows:
        vals = []
        for field in _FIELDS:
            v = row.get(field, "")
            if v == "":
                vals.append(None)
            elif field in _REAL:
                vals.append(float(v))
            elif field in _INT:
                vals.append(int(v))
            else:
                vals.append(v)
        conn.execute(sql, vals)
    conn.commit()
    conn.close()
    print(f"[startup] poi.db rebuilt: {len(rows)} rows")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_db()
    yield


app = FastAPI(title="AI Route Planner", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_graph = build_graph()
_refine_graph = build_refine_graph()

# Simple in-memory cache: key → final RouteState
_cache: dict[str, dict] = {}


def _intent_cache_key(intent: dict, language: str = "zh-TW") -> str:
    """Structured cache key from parsed intent — lang+city+area+budget_tier+sorted_cats+dining_count."""
    city   = intent.get("city", "")
    area   = intent.get("area", "")
    budget = (intent.get("budget_per_person", 0) // 50) * 50  # bucket to nearest 50
    cats   = ",".join(sorted(intent.get("must_include_categories", [])))
    dining = intent.get("dining_count", 0)
    dur    = intent.get("duration_hours", 0)
    return f"{language}|{city}|{area}|{budget}|{cats}|d{dining}|h{dur}"


async def _stream_route(req: RouteRequest) -> AsyncGenerator[str, None]:
    # Emit immediately so the client sees a response within ~50ms
    yield _sse("step", {"message": i18n.step("planning_start", req.language)})
    await asyncio.sleep(0)  # flush before any blocking I/O

    # Fast check: exact input match (language-scoped)
    raw_key = f"{req.language}:{req.user_input[:100]}"
    if raw_key in _cache:
        cached = _cache[raw_key]
        if req.user_id and cached.get("intent") and cached.get("route"):
            user_memory.update(req.user_id, cached["intent"], cached["route"])
        yield _sse("step", {"message": i18n.step("cache_hit", req.language)})
        await asyncio.sleep(0)
        yield _sse("result", _format_result(cached))
        yield _sse("done", {})
        return

    mem = user_memory.load(req.user_id) if req.user_id else {}

    initial: RouteState = {
        "user_input": req.user_input,
        "language": req.language,
        "intent": {},
        "candidates": {},
        "route": [],
        "locked_nodes": req.locked_nodes,
        "map_url": "",
        "summary": "",
        "fulfillment_notes": {},
        "conversation_history": req.conversation_history,
        "stream_updates": [],
        "user_memory": mem,
        "weather": {},
        "xiaohongshu_post": "",
    }

    prev_steps: list[str] = []
    intent_key: str | None = None
    final_state: RouteState | None = None

    try:
        for chunk in _graph.stream(initial, stream_mode="values"):
            new_steps = chunk.get("stream_updates", [])
            for step in new_steps[len(prev_steps):]:
                yield _sse("step", {"message": step})
                await asyncio.sleep(0)  # flush each event before next blocking node
            prev_steps = new_steps
            final_state = chunk

            # After IntentNode resolves, check intent-based cache
            if intent_key is None and chunk.get("intent"):
                intent_key = _intent_cache_key(chunk["intent"], req.language)
                if intent_key in _cache:
                    cached = _cache[intent_key]
                    if req.user_id and cached.get("intent") and cached.get("route"):
                        user_memory.update(req.user_id, cached["intent"], cached["route"])
                    yield _sse("step", {"message": i18n.step("intent_cache", req.language)})
                    await asyncio.sleep(0)
                    yield _sse("result", _format_result(cached))
                    yield _sse("done", {})
                    return

    except Exception as exc:
        yield _sse("error", {"message": str(exc)})
        return

    if final_state:
        _cache[raw_key] = final_state
        if intent_key:
            _cache[intent_key] = final_state
        if req.user_id and final_state.get("intent") and final_state.get("route"):
            user_memory.update(req.user_id, final_state["intent"], final_state["route"])

        # Send route result immediately (template xiaohongshu already included)
        yield _sse("result", _format_result(final_state))
        await asyncio.sleep(0)

        # Generate LLM xiaohongshu post asynchronously after route result is delivered
        yield _sse("step", {"message": i18n.step("xhs_generating", req.language)})
        await asyncio.sleep(0)
        try:
            from route_planner.nodes.output import _llm_xiaohongshu
            loop = asyncio.get_event_loop()
            xhs_post = await loop.run_in_executor(
                None,
                lambda: _llm_xiaohongshu(
                    final_state["route"],
                    final_state.get("intent", {}),
                    final_state.get("weather", {}),
                    req.language,
                ),
            )
            if xhs_post:
                updated = {**final_state, "xiaohongshu_post": xhs_post}
                _cache[raw_key] = updated
                if intent_key:
                    _cache[intent_key] = updated
                yield _sse("step", {"message": i18n.step("xhs_done", req.language)})
                await asyncio.sleep(0)
                yield _sse("xiaohongshu_update", {"xiaohongshu_post": xhs_post})
                await asyncio.sleep(0)
        except Exception:
            pass  # template version already sent in result SSE

    yield _sse("done", {})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _format_result(state: RouteState) -> dict:
    return {
        "route": state.get("route", []),
        "map_url": state.get("map_url", ""),
        "summary": state.get("summary", ""),
        "fulfillment_notes": state.get("fulfillment_notes", {}),
        "agent_steps": state.get("stream_updates", []),
        "weather": state.get("weather", {}),
        "xiaohongshu_post": state.get("xiaohongshu_post", ""),
    }


@app.post("/route/generate")
async def generate_route(req: RouteRequest):
    return StreamingResponse(
        _stream_route(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_refine(req: RouteRequest) -> AsyncGenerator[str, None]:
    if not req.current_route:
        yield _sse("error", {"message": "current_route is required for refine"})
        yield _sse("done", {})
        return

    initial: RouteState = {
        "user_input": req.user_input,
        "language": req.language,
        "intent": {},
        "candidates": {},
        "route": req.current_route,
        "locked_nodes": req.locked_nodes,
        "map_url": "",
        "summary": "",
        "fulfillment_notes": {},
        "conversation_history": req.conversation_history,
        "stream_updates": [],
        "user_memory": user_memory.load(req.user_id) if req.user_id else {},
        "weather": {},
        "xiaohongshu_post": "",
    }

    prev_steps: list[str] = []
    final_state: RouteState | None = None
    try:
        for chunk in _refine_graph.stream(initial, stream_mode="values"):
            new_steps = chunk.get("stream_updates", [])
            for step in new_steps[len(prev_steps):]:
                yield _sse("step", {"message": step})
                await asyncio.sleep(0)  # flush each event before next blocking node
            prev_steps = new_steps
            final_state = chunk
    except Exception as exc:
        yield _sse("error", {"message": str(exc)})
        return

    if final_state:
        if req.user_id and final_state.get("route"):
            user_memory.update(req.user_id, final_state.get("intent", {}), final_state["route"])
        yield _sse("result", _format_result(final_state))

    yield _sse("done", {})


@app.post("/route/refine")
async def refine_route(req: RouteRequest):
    return StreamingResponse(
        _stream_refine(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
