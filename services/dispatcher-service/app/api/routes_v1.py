"""Versioned `/api/v1/...` endpoints for the dispatcher service.

Hosts new contracts that conform to the PRD §6.2 / §9.2 specifications:

* ``GET  /api/v1/transport-requests``  — adapter over ``transport_requests``
* ``GET  /api/v1/metrics/business``    — order accuracy + avg utilization

The legacy un-versioned router (``app/api/routes.py``) is also re-mounted
under ``/api/v1`` from ``main.py`` for backward compatibility, so this module
only contains the *new* surface.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import (
    BusinessMetricsResponse,
    TransportRequestPRD,
    TransportRequestRecent,
    TransportRequestRecentListResponse,
    TransportRequestsListResponse,
)
from app.storage import postgres

logger = logging.getLogger(__name__)

router = APIRouter()


def _ensure_range(range_from: datetime, range_to: datetime) -> None:
    if range_from >= range_to:
        raise HTTPException(
            status_code=422,
            detail="'from' must be strictly earlier than 'to'",
        )


@router.get("/transport-requests", response_model=TransportRequestsListResponse)
async def list_transport_requests(
    office_id: int = Query(..., ge=0, description="Office / warehouse identifier"),
    range_from: datetime = Query(..., alias="from", description="Window start (ISO 8601)"),
    range_to: datetime = Query(..., alias="to", description="Window end (ISO 8601)"),
) -> TransportRequestsListResponse:
    """Return transport requests for ``office_id`` in ``[from, to]`` (PRD §6.2)."""
    _ensure_range(range_from, range_to)

    rows = await postgres.get_transport_requests_window(
        office_id=office_id,
        range_from=range_from,
        range_to=range_to,
    )

    items = [
        TransportRequestPRD(
            id=int(row["id"]),
            office_from_id=int(row["office_from_id"]),
            time_window_start=row["time_window_start"],
            time_window_end=row["time_window_end"],
            routes=[int(rt) for rt in (row.get("routes") or [])],
            total_predicted_units=float(row["total_predicted_units"]),
            vehicles_required=int(row["vehicles_required"]),
            status=str(row["status"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]

    return TransportRequestsListResponse(
        items=items,
        total=len(items),
        office_id=office_id,
        range_from=range_from,
        range_to=range_to,
    )


@router.get(
    "/transport-requests/recent",
    response_model=TransportRequestRecentListResponse,
)
async def list_recent_transport_requests(
    warehouse_id: int | None = Query(None, ge=0, description="Filter by warehouse"),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=1000, description="Max rows to return"),
) -> TransportRequestRecentListResponse:
    """Dashboard-facing list of recent dispatch decisions.

    Distinct from PRD ``GET /transport-requests`` which enforces a time
    window and joins forecasts to populate ``routes[]``. This endpoint is
    a plain row listing for the dashboard dispatch page.
    """
    rows = await postgres.list_recent_transport_requests(
        warehouse_id=warehouse_id,
        status=status,
        limit=limit,
    )
    items = [TransportRequestRecent(**row) for row in rows]
    return TransportRequestRecentListResponse(items=items, total=len(items))


@router.get("/metrics/business", response_model=BusinessMetricsResponse)
async def business_metrics(
    range_from: datetime | None = Query(None, alias="from", description="Window start (ISO 8601)"),
    range_to: datetime | None = Query(None, alias="to", description="Window end (ISO 8601)"),
) -> BusinessMetricsResponse:
    """Return PRD §9.2 KPIs computed over slots with actual fulfilment data."""
    if range_from is not None and range_to is not None:
        _ensure_range(range_from, range_to)

    summary = await postgres.get_business_metrics(
        range_from=range_from,
        range_to=range_to,
    )

    note: str | None = None
    if summary["n_slots_evaluated"] == 0:
        note = (
            "No slots have actual fulfilment data yet — KPIs will populate "
            "once transport_requests.actual_vehicles is backfilled."
        )

    return BusinessMetricsResponse(
        order_accuracy=float(summary["order_accuracy"]),
        avg_truck_utilization=float(summary["avg_truck_utilization"]),
        n_slots_evaluated=int(summary["n_slots_evaluated"]),
        n_slots_total=int(summary["n_slots_total"]),
        truck_capacity=int(summary["truck_capacity"]),
        range_from=range_from,
        range_to=range_to,
        note=note,
    )
