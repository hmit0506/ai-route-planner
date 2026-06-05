from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RouteRequest(BaseModel):
    user_input: str
    language: str = "zh-TW"   # "zh-TW" | "zh-CN" | "en"
    conversation_history: List[Dict[str, str]] = Field(default_factory=list)
    locked_nodes: List[int] = Field(default_factory=list)
    current_route: List[Dict[str, Any]] = Field(default_factory=list)
    user_id: Optional[str] = None


class GroupBuy(BaseModel):
    title: str
    original_price: float
    current_price: float
    discount: str


class POIItem(BaseModel):
    order: int
    name: str
    category: str
    address: str
    lat: float
    lng: float
    rating: float
    half_year_sales: int
    queue_risk: str
    queue_risk_tip: str
    has_group_buy: bool
    group_buy: Optional[GroupBuy]
    stay_minutes: int
    transport_to_next: str
    trend_tag: str
    business_hours: str
    avg_price_per_person: float


class RouteResponse(BaseModel):
    route: List[POIItem]
    map_url: str
    summary: str
    agent_steps: List[str]
