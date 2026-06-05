import json
from pathlib import Path

_USERS_DIR = Path(__file__).parent / "data" / "users"
_MAX_VISITED = 100
_MAX_PREF = 20
_MAX_BUDGET_HISTORY = 10


def load(user_id: str) -> dict:
    path = _USERS_DIR / f"{user_id}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(user_id: str, memory: dict) -> None:
    _USERS_DIR.mkdir(parents=True, exist_ok=True)
    (_USERS_DIR / f"{user_id}.json").write_text(
        json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def update(user_id: str, intent: dict, route: list[dict]) -> None:
    mem = load(user_id)

    for pref in intent.get("food_pref", []):
        prefs = mem.setdefault("food_pref", [])
        if pref not in prefs:
            prefs.append(pref)
            if len(prefs) > _MAX_PREF:
                prefs.pop(0)

    for a in intent.get("avoid", []):
        avoids = mem.setdefault("avoid", [])
        if a not in avoids:
            avoids.append(a)

    visited = mem.setdefault("visited_poi_ids", [])
    for stop in route:
        pid = stop.get("poi_id") or stop.get("id", "")
        if pid and pid not in visited:
            visited.append(pid)
    if len(visited) > _MAX_VISITED:
        mem["visited_poi_ids"] = visited[-_MAX_VISITED:]

    budget = intent.get("budget_per_person", 0)
    if budget:
        hist = mem.setdefault("budget_history", [])
        hist.append(budget)
        if len(hist) > _MAX_BUDGET_HISTORY:
            hist.pop(0)

    save(user_id, mem)


def build_route_hint(memory: dict, current_intent: dict) -> str:
    """Return a short prompt note for RouteAgent; empty string if nothing useful."""
    if not memory:
        return ""
    parts = []

    hist_food = memory.get("food_pref", [])
    curr_food = current_intent.get("food_pref", [])
    if hist_food and not curr_food:
        parts.append(f"历史菜系偏好（仅当前未指定时参考）：{', '.join(hist_food[:5])}")

    hist_avoid = memory.get("avoid", [])
    curr_avoid = current_intent.get("avoid", [])
    extra_avoid = [a for a in hist_avoid if a not in curr_avoid]
    if extra_avoid:
        parts.append(f"历史忌口（补充约束）：{', '.join(extra_avoid[:5])}")

    budget_hist = memory.get("budget_history", [])
    if len(budget_hist) >= 2:
        avg = round(sum(budget_hist) / len(budget_hist))
        parts.append(f"历史人均消费约{avg}元（仅供参考）")

    if not parts:
        return ""
    return "用户历史偏好（软约束）：\n" + "\n".join(f"- {p}" for p in parts)
