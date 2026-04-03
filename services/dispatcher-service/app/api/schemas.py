from datetime import datetime
from pydantic import BaseModel


class ForecastInput(BaseModel):
    timestamp: datetime
    total_containers: float


class DispatchRequest(BaseModel):
    warehouse_id: int
    forecasts: list[ForecastInput] | None = None
    time_range_start: datetime | None = None
    time_range_end: datetime | None = None


class TransportRequestItem(BaseModel):
    time_slot_start: datetime
    time_slot_end: datetime
    total_containers: float
    truck_capacity: int
    buffer_pct: float
    trucks_needed: int
    calculation: str


class DispatchResponse(BaseModel):
    warehouse_id: int
    dispatch_requests: list[TransportRequestItem]
    config: dict


class ScheduleResponse(BaseModel):
    warehouse_id: int
    schedule: list[dict]


class WarehouseItem(BaseModel):
    warehouse_id: int
    route_count: int
    latest_forecast_at: datetime | None
    upcoming_trucks: int


class WarehouseListResponse(BaseModel):
    warehouses: list[WarehouseItem]
    total: int


class HealthResponse(BaseModel):
    status: str
    database_connected: bool
    uptime_seconds: float
