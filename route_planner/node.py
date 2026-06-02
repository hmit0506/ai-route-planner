from typing import Dict, Any
from route_planner.state import RouteState


class BaseNode:
    def __call__(self, state: RouteState) -> Dict[str, Any]:
        raise NotImplementedError
