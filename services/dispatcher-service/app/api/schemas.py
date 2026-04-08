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
    # Human-readable warehouse label, optional because legacy seed data may
    # leave it NULL. The dashboard falls back to "Warehouse {id}" client-side
    # when this is missing.
    name: str | None = None
    route_count: int
    latest_forecast_at: datetime | None
    upcoming_trucks: int


class WarehouseListResponse(BaseModel):
    warehouses: list[WarehouseItem]
    total: int


class TransportRequestRecent(BaseModel):
    """Row shape for ``GET /api/v1/transport-requests/recent``.

    Mirrors the dispatch table on the dashboard — raw slot fields plus
    fulfilment counters. Kept separate from ``TransportRequestPRD`` so the
    PRD contract can evolve independently of the dashboard list view.
    """

    id: int
    warehouse_id: int
    time_slot_start: datetime
    time_slot_end: datetime
    total_containers: float
    truck_capacity: int
    buffer_pct: float
    trucks_needed: int
    calculation: str | None = None
    status: str
    actual_vehicles: int | None = None
    actual_units: float | None = None
    created_at: datetime
    updated_at: datetime | None = None


class TransportRequestRecentListResponse(BaseModel):
    items: list[TransportRequestRecent]
    total: int


class HealthResponse(BaseModel):
    status: str
    database_connected: bool
    uptime_seconds: float


# ---------------------------------------------------------------------------
# PRD §6.2 — GET /api/v1/transport-requests
# ---------------------------------------------------------------------------


class TransportRequestPRD(BaseModel):
    """Transport request shape mandated by PRD §6.2."""

    id: int
    office_from_id: int
    time_window_start: datetime
    time_window_end: datetime
    routes: list[int]
    total_predicted_units: float
    vehicles_required: int
    status: str
    created_at: datetime


class TransportRequestsListResponse(BaseModel):
    items: list[TransportRequestPRD]
    total: int
    office_id: int
    range_from: datetime
    range_to: datetime


# ---------------------------------------------------------------------------
# Business metrics (PRD §9.2)
# ---------------------------------------------------------------------------


class BusinessMetricsResponse(BaseModel):
    """Two business KPIs surfaced to the dashboard (PRD §9.2).

    * ``order_accuracy``: share of fulfilled slots where the predicted
      vehicle count is within ±2 of the actual one. The ±2 tolerance
      *includes* corner cases such as ``actual=0, predicted=1`` and
      ``actual=0, predicted=2`` — slight over-ordering still counts as
      accurate enough; the cost of those extra trips is captured
      separately by ``avg_truck_utilization``, which drops toward zero
      when trucks roll empty. The two metrics are complementary on
      purpose.
    * ``avg_truck_utilization``: mean of ``actual_units / (vehicles * capacity)``
      across slots that actually shipped.
    """

    order_accuracy: float
    avg_truck_utilization: float
    n_slots_evaluated: int
    n_slots_total: int
    truck_capacity: int
    range_from: datetime | None = None
    range_to: datetime | None = None
    note: str | None = None
