import argparse
import sys
import time
from itertools import permutations, product
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from route_planner.integrations.amap_client import (
    build_static_map_url,
    estimate_route_travel_minutes,
    filter_pois_by_constraints,
    normalize_route_mode,
    search_poi,
    approx_segment_minutes,
)


def _f(v: Any, d: float = 0.0) -> float:
    try:
        if v in (None, ""):
            return d
        return float(v)
    except Exception:
        return d


def _i(v: Any, d: int = 0) -> int:
    try:
        if v in (None, ""):
            return d
        return int(float(v))
    except Exception:
        return d


def parse_args():
    p = argparse.ArgumentParser("Test amap client with constraints + 10s SLA")

    p.add_argument("--city", default="上海")
    p.add_argument("--area", default="外滩")
    p.add_argument("--mode", default="walking", help="walking/driving/transit/bicycling 或中文")
    p.add_argument("--size", default="900*500")

    # POI属性约束
    p.add_argument("--min-rating", type=float, default=0.0)
    p.add_argument("--max-avg-price", type=int, default=99999)
    p.add_argument("--min-sales", type=int, default=0)
    p.add_argument("--max-queue-risk", choices=["低", "中", "高"], default="高")
    p.add_argument("--require-group-buy", action="store_true")

    # 路线约束
    p.add_argument("--max-total-minutes", type=int, default=9999, help="交通+停留")
    p.add_argument("--stay-minutes", type=int, default=60, help="每个POI默认停留")
    p.add_argument("--budget", type=int, default=999999)
    p.add_argument("--party-size", type=int, default=1)

    # 性能控制
    p.add_argument("--fetch-limit", type=int, default=6, help="每类召回量")
    p.add_argument("--top-k-per-cat", type=int, default=3, help="每类参与组合TopK")
    p.add_argument("--max-route-evals", type=int, default=10, help="精算(调用路径API)最多评估路线条数")
    p.add_argument("--time-budget-sec", type=float, default=9.5, help="总时间预算，建议<10秒")
    p.add_argument("--api-timeout-sec", type=float, default=2.0, help="单次路径API超时")

    return p.parse_args()


def build_constraints(args) -> Dict[str, Any]:
    return {
        "min_rating": args.min_rating,
        "max_avg_price_per_person": args.max_avg_price,
        "min_half_year_sales": args.min_sales,
        "max_queue_risk": args.max_queue_risk,
        "require_group_buy": args.require_group_buy,
        "transport_mode": normalize_route_mode(args.mode),
        "max_total_minutes": args.max_total_minutes,
        "budget": args.budget,
        "party_size": max(1, args.party_size),
        "default_stay_minutes": max(0, args.stay_minutes),
    }


def route_cost(route: List[Dict[str, Any]], party_size: int) -> int:
    return sum(_i(p.get("avg_price_per_person"), 0) * party_size for p in route)


def route_score(route: List[Dict[str, Any]]) -> float:
    # 简化打分：评分和销量
    rating_sum = sum(_f(p.get("rating"), 0.0) for p in route)
    sales_sum = sum(_i(p.get("half_year_sales"), 0) for p in route)
    return rating_sum * 1000 + sales_sum * 0.02


def approx_route_minutes(route: List[Dict[str, Any]], mode: str, stay_minutes: int) -> int:
    if len(route) < 2:
        return stay_minutes * len(route)
    travel = 0
    for a, b in zip(route[:-1], route[1:]):
        travel += approx_segment_minutes(a, b, mode=mode)
    return travel + stay_minutes * len(route)


def generate_routes(food: List[Dict[str, Any]], culture: List[Dict[str, Any]], fun: List[Dict[str, Any]], top_k: int) -> List[List[Dict[str, Any]]]:
    out: List[List[Dict[str, Any]]] = []
    for f, c, e in product(food[:top_k], culture[:top_k], fun[:top_k]):
        for perm in permutations([f, c, e], 3):
            out.append(list(perm))
    return out


def choose_route_with_deadline(
    routes: List[List[Dict[str, Any]]],
    constraints: Dict[str, Any],
    time_budget_sec: float,
    api_timeout_sec: float,
    max_route_evals: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], int, int]:
    """
    返回: feasible_best, fallback_best, approx_checked, exact_checked
    """
    mode = normalize_route_mode(constraints["transport_mode"])
    deadline = time.time() + max(1.0, time_budget_sec)
    party_size = _i(constraints["party_size"], 1)
    budget = _i(constraints["budget"], 10**9)
    max_total = _i(constraints["max_total_minutes"], 10**9)
    stay_minutes = _i(constraints["default_stay_minutes"], 60)

    # 1) 粗排
    coarse: List[Tuple[float, int, int, List[Dict[str, Any]]]] = []
    for r in routes:
        total_minutes_approx = approx_route_minutes(r, mode=mode, stay_minutes=stay_minutes)
        total_cost = route_cost(r, party_size=party_size)
        score = route_score(r)

        # 优先可行 + 高分 + 短时
        feasible_flag = 1 if (total_minutes_approx <= max_total and total_cost <= budget) else 0
        coarse.append((feasible_flag, score, total_minutes_approx, r))

    coarse.sort(key=lambda x: (-x[0], -x[1], x[2]))
    approx_checked = len(coarse)

    # 只精算前N条
    shortlisted = coarse[: max(1, max_route_evals)]

    feasible_best = None
    fallback_best = None
    exact_checked = 0

    for feasible_flag, coarse_score, coarse_minutes, route in shortlisted:
        if time.time() >= deadline:
            break

        # 2) 精算（API），有deadline，超时自动降级（amap_client内部已做）
        travel = estimate_route_travel_minutes(
            route=route,
            mode=mode,
            request_timeout_sec=api_timeout_sec,
            deadline_ts=deadline,
        )
        if travel is None:
            # 不可估算就用粗略
            total_minutes = coarse_minutes
        else:
            total_minutes = travel + stay_minutes * len(route)

        total_cost = route_cost(route, party_size=party_size)
        score = route_score(route)

        cand = {
            "route": route,
            "travel_minutes": travel if travel is not None else (total_minutes - stay_minutes * len(route)),
            "total_minutes": total_minutes,
            "total_cost": total_cost,
            "score": score,
        }

        exact_checked += 1

        # fallback：总时长短优先，其次分高
        if (
            fallback_best is None
            or cand["total_minutes"] < fallback_best["total_minutes"]
            or (
                cand["total_minutes"] == fallback_best["total_minutes"]
                and cand["score"] > fallback_best["score"]
            )
        ):
            fallback_best = cand

        if cand["total_minutes"] <= max_total and cand["total_cost"] <= budget:
            if (
                feasible_best is None
                or cand["score"] > feasible_best["score"]
                or (
                    cand["score"] == feasible_best["score"]
                    and cand["total_minutes"] < feasible_best["total_minutes"]
                )
            ):
                feasible_best = cand

    return feasible_best, fallback_best, approx_checked, exact_checked


def main():
    args = parse_args()
    constraints = build_constraints(args)
    mode = normalize_route_mode(args.mode)

    t0 = time.time()

    # 1) 拉候选
    food = search_poi(args.city, args.area, "餐饮", limit=args.fetch_limit, timeout_sec=args.api_timeout_sec)
    culture = search_poi(args.city, args.area, "文化", limit=args.fetch_limit, timeout_sec=args.api_timeout_sec)
    fun = search_poi(args.city, args.area, "娱乐", limit=args.fetch_limit, timeout_sec=args.api_timeout_sec)

    print(f"初始候选: 餐饮={len(food)} 文化={len(culture)} 娱乐={len(fun)}")

    # 2) 按属性约束过滤
    food = filter_pois_by_constraints(food, constraints)
    culture = filter_pois_by_constraints(culture, constraints)
    fun = filter_pois_by_constraints(fun, constraints)

    print(f"筛选后候选: 餐饮={len(food)} 文化={len(culture)} 娱乐={len(fun)}")

    if not food or not culture or not fun:
        print("筛选条件过严，至少有一类为空，请放宽参数。")
        return

    # 3) 生成路线 + 粗排 + 精算（deadline）
    routes = generate_routes(food, culture, fun, top_k=max(1, args.top_k_per_cat))
    feasible, fallback, approx_checked, exact_checked = choose_route_with_deadline(
        routes=routes,
        constraints=constraints,
        time_budget_sec=args.time_budget_sec,
        api_timeout_sec=args.api_timeout_sec,
        max_route_evals=args.max_route_evals,
    )

    picked = feasible if feasible is not None else fallback
    if picked is None:
        print("无法生成路线。")
        return

    if feasible is None:
        print("⚠️ 未找到严格满足时长/预算的路线，返回最优备选。")

    route = picked["route"]

    # 4) 出图
    map_url = build_static_map_url(
        route=route,
        size=args.size,
        use_roadnet=True,
        route_mode=mode,
        api_timeout_sec=args.api_timeout_sec,
    )

    elapsed = time.time() - t0

    print("\n=== 最终路线 ===")
    for i, p in enumerate(route, 1):
        print(
            f"{i}. {p.get('name')} | {p.get('category')} | rating={p.get('rating')} | "
            f"¥{p.get('avg_price_per_person')}/人 | queue={p.get('queue_risk')}"
        )

    print(f"\n交通方式: {mode}")
    print(f"交通耗时(分钟): {picked['travel_minutes']}")
    print(f"总时长(交通+停留,分钟): {picked['total_minutes']}")
    print(f"总预算估算(元): {picked['total_cost']}")
    print(f"组合粗排数: {approx_checked}, 精算数: {exact_checked}")
    print(f"总耗时(秒): {elapsed:.2f}")

    print("\n地图URL:")
    print(map_url)


if __name__ == "__main__":
    main()
