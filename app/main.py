import json
import os
from typing import AsyncGenerator

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.schemas import RouteRequest
from route_planner.graph import build_graph, build_refine_graph
from route_planner.state import RouteState

app = FastAPI(title="AI Route Planner", version="1.0.0")

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


def _intent_cache_key(intent: dict) -> str:
    """Structured cache key from parsed intent — city+area+budget_tier+sorted_cats+dining_count."""
    city   = intent.get("city", "")
    area   = intent.get("area", "")
    budget = (intent.get("budget_per_person", 0) // 50) * 50  # bucket to nearest 50
    cats   = ",".join(sorted(intent.get("must_include_categories", [])))
    dining = intent.get("dining_count", 0)
    dur    = intent.get("duration_hours", 0)
    return f"{city}|{area}|{budget}|{cats}|d{dining}|h{dur}"


async def _stream_route(req: RouteRequest) -> AsyncGenerator[str, None]:
    # Fast check: exact input match
    raw_key = req.user_input[:100]
    if raw_key in _cache:
        yield _sse("step", {"message": "缓存命中，直接返回结果"})
        yield _sse("result", _format_result(_cache[raw_key]))
        yield _sse("done", {})
        return

    initial: RouteState = {
        "user_input": req.user_input,
        "intent": {},
        "candidates": {},
        "route": [],
        "locked_nodes": req.locked_nodes,
        "map_url": "",
        "summary": "",
        "fulfillment_notes": {},
        "conversation_history": req.conversation_history,
        "stream_updates": [],
    }

    prev_steps: list[str] = []
    intent_key: str | None = None
    final_state: RouteState | None = None

    try:
        for chunk in _graph.stream(initial, stream_mode="values"):
            new_steps = chunk.get("stream_updates", [])
            for step in new_steps[len(prev_steps):]:
                yield _sse("step", {"message": step})
            prev_steps = new_steps
            final_state = chunk

            # After IntentNode resolves, check intent-based cache
            if intent_key is None and chunk.get("intent"):
                intent_key = _intent_cache_key(chunk["intent"])
                if intent_key in _cache:
                    yield _sse("step", {"message": "意图缓存命中，直接返回结果"})
                    yield _sse("result", _format_result(_cache[intent_key]))
                    yield _sse("done", {})
                    return

    except Exception as exc:
        yield _sse("error", {"message": str(exc)})
        return

    if final_state:
        # Store under both raw input key and intent-based key
        _cache[raw_key] = final_state
        if intent_key:
            _cache[intent_key] = final_state
        yield _sse("result", _format_result(final_state))

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
        "intent": {},
        "candidates": {},
        "route": req.current_route,
        "locked_nodes": req.locked_nodes,
        "map_url": "",
        "summary": "",
        "fulfillment_notes": {},
        "conversation_history": req.conversation_history,
        "stream_updates": [],
    }

    prev_steps: list[str] = []
    final_state: RouteState | None = None
    try:
        for chunk in _refine_graph.stream(initial, stream_mode="values"):
            new_steps = chunk.get("stream_updates", [])
            for step in new_steps[len(prev_steps):]:
                yield _sse("step", {"message": step})
            prev_steps = new_steps
            final_state = chunk
    except Exception as exc:
        yield _sse("error", {"message": str(exc)})
        return

    if final_state:
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
